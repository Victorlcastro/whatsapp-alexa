"""Ponte WuzAPI -> Alexa.

Recebe os webhooks da WuzAPI (porta 8090), aplica as regras de config.json
(contatos monitorados, menções em grupos) e avisa:
  - falando pelo PC (ex.: Echo Dot pareado via Bluetooth) com edge-tts, e/ou
  - disparando uma rotina da Alexa via TRIGGERcmd, e/ou
  - anúncio na Alexa via Voice Monkey (api-v3 /announce), e/ou
  - um webhook/URL qualquer.
Também serve a interface de configuração em http://127.0.0.1:8090
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(BASE)
CONFIG = os.path.join(BASE, "config.json")
LOG = os.path.join(BASE, "ponte.log")
PORTA = 8090
WUZ = "http://127.0.0.1:8080"


def ler_env():
    env = {}
    with open(os.path.join(RAIZ, ".env"), encoding="utf-8") as f:
        for linha in f:
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.strip().split("=", 1)
                env[k] = v
    return env


ENV = ler_env()
TOKEN = ENV["WUZAPI_USER_TOKEN"]
# Python usado para falar (precisa ter edge-tts); padrão: o mesmo que roda a ponte
TTS_PYTHON = ENV.get("PONTE_TTS_PYTHON") or sys.executable.replace("pythonw.exe", "python.exe")

PADRAO = {
    "ativo": True,
    "contatos": [],  # {numero, nome, frase, ativo}
    "qualquer_contato": False,
    "frase_padrao": "{nome} te mandou mensagem no WhatsApp",
    "ler_mensagem": False,
    "mencoes": {
        "ativo": True,
        "frase": "{nome} te marcou no grupo {grupo}",
        "respostas_contam": True,
        "grupos": [],  # vazio = todos
    },
    "cooldown_seg": 60,
    "silencio": {"ativo": False, "inicio": "23:00", "fim": "07:00"},
    "saidas": {
        "pc": {"ativo": True, "som": True, "reforco": 14},
        "voicemonkey": {"ativo": False, "token": "", "device": "", "voice": "Ricardo", "chime": ""},
        "webhook": {"ativo": False, "url": ""},
        "triggercmd": {"ativo": False, "computador": os.environ.get("COMPUTERNAME", ""), "comando": "Aviso Zap"},
    },
}

lock_cfg = threading.Lock()
eventos = deque(maxlen=80)
ultimo_aviso = {}
fila_fala = queue.Queue()
cache = {"meus": set(), "grupos": {}, "lids": {}, "grupos_ts": 0, "meus_ts": 0}


def log(msg):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(linha, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


def mesclar(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = mesclar(out[k], v)
        else:
            out[k] = v
    return out


def carregar_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return mesclar(PADRAO, json.load(f))
    except (OSError, ValueError):
        return json.loads(json.dumps(PADRAO))


def salvar_config(cfg):
    with lock_cfg:
        tmp = CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG)


# ---------------- WuzAPI ----------------

def wuz(metodo, caminho, corpo=None, timeout=15):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(WUZ + caminho, data=dados, method=metodo,
                                 headers={"Token": TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            resp = json.loads(e.read() or b"{}")
        except ValueError:
            resp = {"success": False, "error": str(e)}
    data = resp.get("data")
    if isinstance(data, str):
        try:
            resp["data"] = json.loads(data)
        except ValueError:
            pass
    return resp


def digitos(s):
    return re.sub(r"\D", "", str(s or "").split("@")[0].split(":")[0])


def variantes(num):
    """Números BR: considera com e sem o 9º dígito."""
    d = digitos(num)
    v = {d}
    if d.startswith("55") and len(d) == 13 and d[4] == "9":
        v.add(d[:4] + d[5:])
    elif d.startswith("55") and len(d) == 12:
        v.add(d[:4] + "9" + d[4:])
    return v


def buscar_lid(numero):
    d = digitos(numero)
    if d in cache["lids"]:
        return cache["lids"][d]
    lids = set()
    for v in variantes(d):
        r = wuz("GET", f"/user/lid/{v}@s.whatsapp.net", timeout=8)
        dd = r.get("data") if isinstance(r.get("data"), dict) else {}
        if dd.get("lid"):
            lids.add(digitos(dd["lid"]))
    if lids:
        cache["lids"][d] = lids
    return lids


def meus_ids():
    if cache["meus"] and time.time() - cache["meus_ts"] < 600:
        return cache["meus"]
    st = wuz("GET", "/session/status", timeout=8).get("data") or {}
    jid = st.get("jid") or ""
    if jid:
        ids = set(variantes(jid)) | buscar_lid(jid)
        cache["meus"], cache["meus_ts"] = ids, time.time()
    return cache["meus"]


def nome_grupo(jid):
    if jid not in cache["grupos"] or time.time() - cache["grupos_ts"] > 1800:
        atualizar_grupos()
    return cache["grupos"].get(jid, "um grupo")


def atualizar_grupos():
    r = wuz("GET", "/group/list", timeout=30)
    d = r.get("data") or {}
    grupos = d.get("Groups") if isinstance(d, dict) else None
    if grupos:
        cache["grupos"] = {g.get("JID"): g.get("Name") or g.get("JID") for g in grupos}
        cache["grupos_ts"] = time.time()
    return cache["grupos"]


# ---------------- regras ----------------

def achar_chaves(obj, nome, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == nome:
                out.append(v)
            achar_chaves(v, nome, out)
    elif isinstance(obj, list):
        for x in obj:
            achar_chaves(x, nome, out)
    return out


def texto_da_msg(msg):
    if not isinstance(msg, dict):
        return ""
    if msg.get("conversation"):
        return msg["conversation"]
    for chave in ("extendedTextMessage",):
        if isinstance(msg.get(chave), dict) and msg[chave].get("text"):
            return msg[chave]["text"]
    for k, v in msg.items():
        if isinstance(v, dict) and v.get("caption"):
            return v["caption"]
    for k in msg:
        if k.endswith("Message"):
            return {"imageMessage": "[foto]", "audioMessage": "[áudio]", "videoMessage": "[vídeo]",
                    "stickerMessage": "[figurinha]", "documentMessage": "[documento]"}.get(k, "")
    return ""


def contato_por_ids(cfg, ids):
    for c in cfg["contatos"]:
        if not c.get("ativo", True):
            continue
        cand = variantes(c["numero"]) | buscar_lid(c["numero"])
        if cand & ids:
            return c
    return None


def dentro_silencio(cfg):
    s = cfg["silencio"]
    if not s.get("ativo"):
        return False
    agora = datetime.now().strftime("%H:%M")
    ini, fim = s["inicio"], s["fim"]
    return (ini <= agora < fim) if ini <= fim else (agora >= ini or agora < fim)


def processar(payload):
    cfg = carregar_config()
    if payload.get("type") != "Message":
        return
    ev = payload.get("event") or {}
    info = ev.get("Info") or {}
    msg = ev.get("Message") or {}
    if info.get("IsFromMe"):
        return
    # ignora reações, recibos de protocolo etc.
    if any(k in msg for k in ("protocolMessage", "reactionMessage", "senderKeyDistributionMessage")) and not texto_da_msg(msg):
        return
    chat = info.get("Chat", "")
    if chat.endswith("@broadcast") or chat.endswith("@newsletter"):
        return
    try:
        ts = datetime.fromisoformat(str(info.get("Timestamp", "")).replace("Z", "+00:00")).timestamp()
        if time.time() - ts > 180:
            return  # mensagem antiga (sincronização ao reconectar)
    except ValueError:
        pass

    is_group = bool(info.get("IsGroup")) or chat.endswith("@g.us")
    remetente_ids = set()
    for k in ("Sender", "SenderAlt"):
        if info.get(k):
            remetente_ids |= variantes(info[k])
    if not is_group:
        for k in ("Chat", "RecipientAlt"):
            if info.get(k):
                remetente_ids |= variantes(info[k])
    pushname = info.get("PushName") or ""
    texto = texto_da_msg(msg)
    numero_vis = next((i for i in sorted(remetente_ids) if len(i) <= 13), "")
    contato = contato_por_ids(cfg, remetente_ids)
    nome = (contato or {}).get("nome") or pushname or numero_vis or "Alguém"

    registro = {"hora": datetime.now().strftime("%H:%M:%S"), "nome": nome, "pushname": pushname,
                "numero": numero_vis, "grupo": "", "texto": texto[:120], "acao": ""}

    frase = None
    chave = None
    if is_group:
        grupo = nome_grupo(chat)
        registro["grupo"] = grupo
        mc = cfg["mencoes"]
        if mc.get("ativo") and (not mc["grupos"] or chat in mc["grupos"]):
            meus = meus_ids()
            marcados = set()
            for lst in achar_chaves(msg, "mentionedjid", []):
                for j in lst or []:
                    marcados |= variantes(j)
            if mc.get("respostas_contam"):
                for ctx in achar_chaves(msg, "contextinfo", []):
                    if isinstance(ctx, dict) and ctx.get("participant") and ctx.get("quotedMessage"):
                        marcados |= variantes(ctx["participant"])
            if meus and marcados & meus:
                frase = mc["frase"]
                chave = "grupo:" + chat
                registro["acao"] = "menção"
        if not frase:
            registro["acao"] = "ignorado (grupo)"
    else:
        if contato:
            frase = contato.get("frase") or cfg["frase_padrao"]
        elif cfg.get("qualquer_contato"):
            frase = cfg["frase_padrao"]
        chave = "pv:" + (numero_vis or chat)
        if not frase:
            registro["acao"] = "ignorado (não monitorado)"
        else:
            registro["acao"] = "aviso"

    if frase:
        if not cfg.get("ativo"):
            frase, registro["acao"] = None, "pausado"
        elif dentro_silencio(cfg):
            frase, registro["acao"] = None, "silêncio"
        elif time.time() - ultimo_aviso.get(chave, 0) < cfg.get("cooldown_seg", 60):
            frase, registro["acao"] = None, "cooldown"

    if frase:
        ultimo_aviso[chave] = time.time()
        fala = frase.format_map(DictPadrao(nome=nome, grupo=registro["grupo"], mensagem=texto, numero=numero_vis))
        if cfg.get("ler_mensagem") and texto and "{mensagem}" not in frase:
            fala += ". " + texto[:200]
        registro["fala"] = fala
        avisar(cfg, fala)
    eventos.appendleft(registro)
    log(f"{registro['acao']}: {nome} {registro['grupo']} | {texto[:60]!r}")


class DictPadrao(dict):
    def __missing__(self, k):
        return ""


# ---------------- saídas ----------------

def avisar(cfg, fala):
    s = cfg["saidas"]
    if s["pc"].get("ativo"):
        fila_fala.put((fala, s["pc"].get("som", True), s["pc"].get("reforco", 14)))
    if s["voicemonkey"].get("ativo") and s["voicemonkey"].get("token"):
        threading.Thread(target=voicemonkey, args=(s["voicemonkey"], fala), daemon=True).start()
    if s["webhook"].get("ativo") and s["webhook"].get("url"):
        threading.Thread(target=webhook_saida, args=(s["webhook"]["url"], fala), daemon=True).start()
    if s["triggercmd"].get("ativo"):
        threading.Thread(target=triggercmd, args=(s["triggercmd"],), daemon=True).start()


TCMD_TOKEN = os.path.join(os.path.expanduser("~"), ".TRIGGERcmdData", "token.tkn")
tcmd_ultimo = [0.0]


def triggercmd(tc, forcar=False):
    """Dispara um comando do TRIGGERcmd; uma rotina da Alexa
    ('Quando: Casa inteligente -> <comando>') fala o aviso."""
    if not forcar and time.time() - tcmd_ultimo[0] < 60:
        log("triggercmd: ignorado (limite de 1 por minuto do plano grátis)")
        return False, "limite de 1 por minuto"
    try:
        token = open(TCMD_TOKEN, encoding="utf-8").read().strip()
        corpo = urllib.parse.urlencode({"computer": tc.get("computador") or os.environ.get("COMPUTERNAME", ""),
                                        "trigger": tc.get("comando") or "Aviso Zap"}).encode()
        req = urllib.request.Request("https://www.triggercmd.com/api/run/triggerSave", data=corpo,
                                     headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = r.read()[:200].decode(errors="replace")
        tcmd_ultimo[0] = time.time()
        log(f"triggercmd: {resp}")
        return True, resp
    except Exception as e:
        log(f"triggercmd erro: {e}")
        return False, str(e)


def voicemonkey(vm, fala):
    params = {"token": vm["token"], "device": vm["device"], "speech": fala, "language": "pt-BR"}
    if vm.get("voice"):
        params["voice"] = vm["voice"]
    if vm.get("chime"):
        params["chime"] = vm["chime"]
    url = "https://api-v3.voicemonkey.io/announce?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            log(f"voicemonkey: {r.status} {r.read()[:200]!r}")
            return True, "ok"
    except urllib.error.HTTPError as e:
        corpo = e.read()[:300].decode(errors="replace")
        log(f"voicemonkey erro {e.code}: {corpo}")
        return False, f"{e.code}: {corpo}"
    except Exception as e:
        log(f"voicemonkey erro: {e}")
        return False, str(e)


def webhook_saida(url, fala):
    try:
        u = url.replace("{texto}", urllib.parse.quote(fala))
        if u == url:
            req = urllib.request.Request(url, data=json.dumps({"texto": fala}).encode(),
                                         headers={"Content-Type": "application/json"})
        else:
            req = u
        with urllib.request.urlopen(req, timeout=15) as r:
            log(f"webhook saída: {r.status}")
    except Exception as e:
        log(f"webhook saída erro: {e}")


def trabalhador_fala():
    script = os.path.join(BASE, "falar_aviso.py")
    while True:
        fala, som, reforco = fila_fala.get()
        try:
            subprocess.run([TTS_PYTHON, script, fala, "1" if som else "0", str(reforco)], timeout=90,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            log(f"erro ao falar: {e}")


# ---------------- HTTP ----------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, codigo, corpo, tipo="application/json; charset=utf-8"):
        dados = corpo if isinstance(corpo, bytes) else json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        bruto = self.rfile.read(n) if n else b""
        if "x-www-form-urlencoded" in (self.headers.get("Content-Type") or ""):
            form = urllib.parse.parse_qs(bruto.decode())
            return json.loads(form.get("jsonData", ["{}"])[0])
        return json.loads(bruto or b"{}")

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ("/", "/index.html"):
            with open(os.path.join(BASE, "index.html"), "rb") as f:
                return self.responder(200, f.read(), "text/html; charset=utf-8")
        if p == "/api/config":
            return self.responder(200, carregar_config())
        if p == "/api/eventos":
            return self.responder(200, list(eventos))
        if p == "/api/status":
            st = wuz("GET", "/session/status", timeout=8).get("data") or {}
            return self.responder(200, {"connected": st.get("connected"), "loggedIn": st.get("loggedIn"),
                                        "jid": st.get("jid"), "name": st.get("name")})
        if p == "/api/qr":
            r = wuz("GET", "/session/qr", timeout=10)
            d = r.get("data") or {}
            return self.responder(200, {"qr": d.get("QRCode", "") if isinstance(d, dict) else "", "erro": r.get("error")})
        if p == "/api/grupos":
            cache["grupos_ts"] = 0
            g = atualizar_grupos()
            return self.responder(200, sorted(({"jid": k, "nome": v} for k, v in g.items()), key=lambda x: x["nome"].lower()))
        self.responder(404, {"erro": "não encontrado"})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        try:
            dados = self.corpo()
        except Exception as e:
            return self.responder(400, {"erro": str(e)})
        if p == "/webhook":
            self.responder(200, {"ok": True})
            try:
                processar(dados)
            except Exception as e:
                log(f"erro processando webhook: {e!r}")
            return
        if p == "/api/config":
            cfg = mesclar(PADRAO, dados)
            for c in cfg["contatos"]:
                c["numero"] = digitos(c.get("numero"))
            salvar_config(cfg)
            cache["lids"].clear()
            return self.responder(200, {"ok": True})
        if p == "/api/testar":
            cfg = carregar_config()
            fala = dados.get("texto") or "Teste: Fulano te mandou mensagem no WhatsApp"
            alvo = dados.get("saida")
            s = cfg["saidas"]
            if alvo == "pc":
                fila_fala.put((fala, s["pc"].get("som", True), s["pc"].get("reforco", 14)))
                return self.responder(200, {"ok": True, "msg": "falando no PC"})
            if alvo == "voicemonkey":
                ok, m = voicemonkey(s["voicemonkey"], fala)
                return self.responder(200, {"ok": ok, "msg": m})
            if alvo == "triggercmd":
                ok, m = triggercmd(s["triggercmd"], forcar=True)
                return self.responder(200, {"ok": ok, "msg": m})
            if alvo == "webhook":
                webhook_saida(s["webhook"]["url"], fala)
                return self.responder(200, {"ok": True, "msg": "enviado (veja o log)"})
            avisar(cfg, fala)
            return self.responder(200, {"ok": True, "msg": "enviado para as saídas ativas"})
        if p == "/api/conectar":
            r = wuz("POST", "/session/connect", {"Subscribe": ["Message"], "Immediate": True})
            return self.responder(200, r)
        if p == "/api/parear":
            fone = digitos(dados.get("telefone"))
            if not fone:
                return self.responder(200, {"ok": False, "msg": "informe o número"})
            st = wuz("GET", "/session/status", timeout=8).get("data") or {}
            if not st.get("connected"):
                wuz("POST", "/session/connect", {"Subscribe": ["Message"], "Immediate": True})
                time.sleep(3)
            r = wuz("POST", "/session/pairphone", {"Phone": fone}, timeout=30)
            d = r.get("data") if isinstance(r.get("data"), dict) else {}
            if d.get("LinkingCode"):
                return self.responder(200, {"ok": True, "codigo": d["LinkingCode"]})
            return self.responder(200, {"ok": False, "msg": r.get("error") or str(r)[:200]})
        if p == "/api/desconectar":
            return self.responder(200, wuz("POST", "/session/logout", {}))
        self.responder(404, {"erro": "não encontrado"})


WUZ_EXE = os.path.join(RAIZ, "bin", "wuzapi.exe")
WUZ_DADOS = os.path.join(RAIZ, "data")


def wuz_vivo():
    try:
        with urllib.request.urlopen(WUZ + "/health", timeout=3):
            return True
    except urllib.error.HTTPError:
        return True  # respondeu, mesmo que com erro
    except Exception:
        return False


def iniciar_wuzapi():
    """Sobe o wuzapi.exe (sem janela) se ele não estiver rodando."""
    if wuz_vivo() or not os.path.exists(WUZ_EXE):
        return False
    env = dict(os.environ, WUZAPI_ADMIN_TOKEN=ENV["WUZAPI_ADMIN_TOKEN"],
               WUZAPI_GLOBAL_ENCRYPTION_KEY=ENV["WUZAPI_GLOBAL_ENCRYPTION_KEY"],
               TZ="America/Sao_Paulo", WEBHOOK_FORMAT="json", LOG_LEVEL="info", SESSION_DEVICE_NAME="Alexa Notifier")
    saida = open(os.path.join(RAIZ, "wuzapi.log"), "a", encoding="utf-8")
    subprocess.Popen([WUZ_EXE, "-address=127.0.0.1", "-port=8080", f"-datadir={WUZ_DADOS}",
                      "-logtype=console", "-osname=Alexa Notifier"],
                     cwd=os.path.dirname(WUZ_EXE), env=env, stdout=saida, stderr=saida,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x00000008)  # DETACHED
    log("wuzapi.exe iniciado")
    return True


def garantir_usuario():
    """Na primeira execução, cria na WuzAPI o usuário (instância) com o token do .env."""
    for _ in range(30):
        if wuz_vivo():
            break
        time.sleep(2)
    adm = ENV.get("WUZAPI_ADMIN_TOKEN", "")
    try:
        req = urllib.request.Request(WUZ + "/admin/users", headers={"Authorization": adm})
        with urllib.request.urlopen(req, timeout=10) as r:
            dados = json.loads(r.read() or b"{}").get("data") or []
        if any(u.get("token") == TOKEN for u in dados):
            return
        corpo = json.dumps({"name": "ponte", "token": TOKEN, "events": "Message",
                            "webhook": f"http://127.0.0.1:{PORTA}/webhook"}).encode()
        req = urllib.request.Request(WUZ + "/admin/users", data=corpo, method="POST",
                                     headers={"Authorization": adm, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        log("usuário criado na WuzAPI")
    except Exception as e:
        log(f"não consegui verificar/criar o usuário na WuzAPI: {e}")


def garantir_webhook():
    """Mantém a WuzAPI rodando, apontando para esta ponte e conectada."""
    while True:
        iniciar_wuzapi()
        garantir_usuario()
        st = {}
        for _ in range(60):
            st = wuz("GET", "/session/status", timeout=5)
            if st.get("success"):
                break
            time.sleep(5)
        wuz("POST", "/webhook", {"webhookurl": f"http://127.0.0.1:{PORTA}/webhook", "events": ["Message"]})
        d = st.get("data") or {}
        if d.get("jid") and not d.get("connected"):
            wuz("POST", "/session/connect", {"Subscribe": ["Message"], "Immediate": True})
            log("sessão reconectada")
        while wuz_vivo():  # vigia: se o exe cair, sobe de novo
            time.sleep(30)
        log("wuzapi parou de responder, reiniciando")


if __name__ == "__main__":
    if not os.path.exists(CONFIG):
        salvar_config(json.loads(json.dumps(PADRAO)))

    class Servidor(ThreadingHTTPServer):
        allow_reuse_address = False  # no Windows, reuse permite 2 cópias na mesma porta

        def server_bind(self):
            import socket
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    try:
        srv = Servidor((os.environ.get("PONTE_HOST", "127.0.0.1"), PORTA), Handler)
    except OSError:
        log("outra cópia da ponte já está rodando; saindo")
        sys.exit(0)
    threading.Thread(target=trabalhador_fala, daemon=True).start()
    threading.Thread(target=garantir_webhook, daemon=True).start()
    log(f"ponte ouvindo em http://127.0.0.1:{PORTA}")
    srv.serve_forever()
