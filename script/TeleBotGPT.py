import logging
import json
import os
import asyncio
from pyrogram import Client, filters
from pyrogram.enums import ChatAction
import aiohttp
import re
import socket
import platform
import subprocess
import requests


# Almacena el historial de conversaciones por usuario
CONVERSATION_HISTORY = {}
# ======== Variables globales (añadir al inicio)
USE_API_MODE = False  # False = modo servidor, True = modo API
CURRENT_API = None  # Almacena la API seleccionada

USER_FILES = {}  # {user_id: {"files": {file_id: {"name": str, "chunks": list}}, "current_file": str}}
CHUNK_SIZE = 15000  # ~15K caracteres por chunk
MAX_TELEGRAM_MSG = 4000

#logging.basicConfig(level=logging.DEBUG)  # Habilita logs detallados

def update_config(var_name, var_value, config_file="config.json"):
    if os.path.exists(config_file):
        with open(config_file, "r") as file:
            config = json.load(file)
    else:
        config = {}

    config[var_name] = var_value

    with open(config_file, "w") as file:
        json.dump(config, file, indent=4)

def get_config(var_name, default_value, create_var=False, config_file="config.json"):
    # Verifica si el archivo de configuración existe; si no, carga un diccionario vacío
    if os.path.exists(config_file):
        with open(config_file, "r") as file:
            config = json.load(file)
    else:
        config = {}

    # Devuelve el valor de la variable si existe, o el valor por defecto si no
    value = config.get(var_name, default_value)

    # Si la variable no existe y create_var=True, añade la variable con el valor por defecto
    if var_name not in config and create_var:
        update_config(var_name, default_value, config_file)
    return value

def load_config():
    global TOKEN, API_ID, API_HASH, OLLAMA_SERVERS, OLLAMA_CONTEXT, OLLAMA_TEMPERATURE, SERVER_INDEX, config, MAX_HISTORY
    # Cargar configuración desde config.json
    with open("config.json", "r", encoding="utf-8") as file:
        config = json.load(file)

    TOKEN = config["token"]
    API_ID = config["api_id"]
    API_HASH = config["api_hash"]
    OLLAMA_SERVERS = config["servers"]  # Lista de servidores
    OLLAMA_CONTEXT = config["context"]
    OLLAMA_TEMPERATURE = config["temperature"]
    SERVER_INDEX = config["server_index"]  # Índice del servidor actual
    MAX_HISTORY = config["max_history"]  # Mensajes máximos por usuario en memoria
    AUTHORIZED_USERS = config["authorized_users"]
    # Inicializar el cliente de Pyrogram

load_config()
bot_name=f"TeleBotGPT_{socket.gethostname()}"
bot = Client(f"{bot_name}", bot_token=TOKEN, api_id=API_ID, api_hash=API_HASH)
#bot.start()
print(f"Bot {bot_name} configured")

   
def load_conversation_history(user_id):
    """Carga el historial de conversación desde un archivo JSON si existe."""
    file_path = f"{user_id}.json"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    return []

def save_conversation_history(user_id):
    """Guarda el historial de conversación en un archivo JSON."""
    file_path = f"{user_id}.json"
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(CONVERSATION_HISTORY[user_id], file, ensure_ascii=False, indent=4)

def get_current_server():
    """Devuelve la URL del servidor actual."""
    host, port, alias, model = OLLAMA_SERVERS[SERVER_INDEX]
    return f"http://{host}:{port}/api/generate", model, alias

def update_conversation_history(user_id, user_message, bot_response, source = "Ollama"):
    global MAX_HISTORY, CONVERSATION_HISTORY
    """Añadir mensaje al historial del usuario y limitar a MAX_HISTORY."""
    if user_id not in CONVERSATION_HISTORY:
        CONVERSATION_HISTORY[user_id] = []
    
    # Agregar mensaje del usuario y respuesta del bot
    CONVERSATION_HISTORY[user_id].append(f"User: {user_message}")
    bot_response = remove_thinking_tags(bot_response)
    #CONVERSATION_HISTORY[user_id].append(f"Bot: {bot_response}")
    CONVERSATION_HISTORY[user_id].append(f"Bot[{source}]: {bot_response}")


    # Limitar el historial a los últimos MAX_HISTORY mensajes
    CONVERSATION_HISTORY[user_id] = CONVERSATION_HISTORY[user_id][-MAX_HISTORY:]

    # Guardar el historial actualizado en el archivo JSON
    save_conversation_history(user_id)
    
async def query_ollama(prompt, client, message):
    """Enviar el mensaje al servidor Ollama y obtener la respuesta del LLM."""
    global SERVER_INDEX, CONVERSATION_HISTORY

    user_id = message.from_user.id
    
    context = build_context(user_id, prompt)
    #print(f"Contexto enviado (primeras 100 chars):\n{context[:100]}...")
    
    if user_id not in CONVERSATION_HISTORY:
        CONVERSATION_HISTORY[user_id] = load_conversation_history(user_id)
    
    history = "\n".join(CONVERSATION_HISTORY.get(user_id, []))
    history = truncate_message(history)
    
    for _ in range(len(OLLAMA_SERVERS)):
        OLLAMA_URL, OLLAMA_MODEL, alias = get_current_server()
        
        host, port, _, _ = OLLAMA_SERVERS[SERVER_INDEX]
        
        # -------- Verificación rápida del servidor
        server_status = await check_server_health(host, port, full_check=False)
        if server_status != "online":
            SERVER_INDEX = (SERVER_INDEX + 1) % len(OLLAMA_SERVERS)
            #msgOut=f"⚠️ Servidor {alias} ({host}:{port}) inaccesible. Estado: {server_status}.\nPasando al siguiente servidor {OLLAMA_SERVERS[SERVER_INDEX][0]}"
            msgOut=f"⚠️ Servidor {alias} inaccesible. Estado: {server_status}.\nPasando al siguiente servidor {OLLAMA_SERVERS[SERVER_INDEX][2]}"
            print(msgOut)
            await message.reply(msgOut)
            continue
        
        payload = {
            "model": OLLAMA_MODEL,
            #"prompt": f"{OLLAMA_CONTEXT}\nfile={context}\nchat history trucated=[{history}]\nUser Prompt: {prompt}\nBot:",
            "prompt": f"Contexto:\n{context}\n\nPregunta: {prompt}\nRespuesta:",
            "temperature": OLLAMA_TEMPERATURE,
            "stream": False
        }
        #print (f"\n\n________\n{context}\n\n________\n")
        await client.send_chat_action(message.chat.id, ChatAction.CHOOSE_STICKER)
        msgOut=f"probando servidor:{alias}\n{OLLAMA_SERVERS[SERVER_INDEX]}"
        print(msgOut)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(OLLAMA_URL, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result["response"], alias
                    else:
                        raise Exception(f"Error {response.status}: Unexpected reply")
        except Exception as e:
            msgOut=f"Error con el servidor {OLLAMA_SERVERS[SERVER_INDEX]}: {e}"
            print(msgOut)
            await message.reply(msgOut)
            SERVER_INDEX = (SERVER_INDEX + 1) % len(OLLAMA_SERVERS)
    
    return "No hay servidores disponibles.", "Ollama-Error"

@bot.on_message(filters.private & filters.text)
async def chat_handler(client, message):
    temp_alias=f"API[{CURRENT_API}]" if USE_API_MODE else f"SERVER{OLLAMA_SERVERS[SERVER_INDEX]}"
    print(f"________________________________________________________________________________")
    print(f"{temp_alias}-user={message.from_user.username}:\n{message.text}")
    print(f"____________________")
    
    if not await msg(client, message):
        return

    if message.text.startswith("/"):
        await processCommand(client, message)
        return

    prompt = message.text
    user_id = message.from_user.id
    
    # Construir contexto dinámico
    context = build_context(user_id, prompt)
    
    if USE_API_MODE:
        response, source = await query_api(prompt, CURRENT_API, message)
        alias = CURRENT_API.capitalize()
    else:
        response, alias = await query_ollama(prompt, client, message)
        source = f"Ollama-{alias}"
    
    # Procesar respuesta
    response_no_thinking = remove_thinking_tags(response)
    response = response.replace("<think>", "<blockquote>").replace("</think>", "</blockquote>")
    
    # Encabezado mejorado
    current_file = ""
    if user_id in USER_FILES and USER_FILES[user_id]["current_file"]:
        current_file = f" | 📁 {USER_FILES[user_id]['files'][USER_FILES[user_id]['current_file']]['name']}"
    
    LLM_HEADER = f"<b>{alias}{current_file} Reply</b>:\n"
    
    update_conversation_history(user_id, prompt, response_no_thinking, source)
    await message.reply(f"{LLM_HEADER}{response}")
    print(f"{LLM_HEADER}{response}")

def format_thinking_tags(text):
    """
    Formatea los thinking tags con:
    - Líneas separadas para cada bloque
    - Estilo visual distintivo
    """
    processed = re.sub(
        r'<think>(.*?)</think>',
        r'\n💭 \1\n',  # Emoji + nueva línea
        text,
        flags=re.DOTALL
    )
    return processed.strip()

async def chat_handler1(client, message):
    """Procesar mensajes privados."""
    temp_alias=f"API[{CURRENT_API}]" if USE_API_MODE else f"SERVER{OLLAMA_SERVERS[SERVER_INDEX]}"
    print(f"________________________________________________________________________________")
    print(f"{temp_alias}-user={message.from_user.username}:\n{message.text}")
    print(f"____________________")
    
    if not await msg(client, message):
        return

    if message.text.startswith("/"):
        await processCommand(client, message)
        return

    prompt = message.text
    user_id = message.from_user.id
    
    if USE_API_MODE:
        # Modo API
        response, source = await query_api(prompt, CURRENT_API, message)
        alias = CURRENT_API.capitalize()  # Usamos el nombre de la API como alias
        server_info = f"[API]"  # No usamos SERVER_INDEX para APIs
    else:
        # Modo servidor local
        response, alias = await query_ollama(prompt, client, message)
        source = f"Ollama-{alias}"
        server_info = f"[{SERVER_INDEX}]"  # Mostramos índice del servidor
        
    response_no_thinking = remove_thinking_tags(response)
    response = response.replace("<think>", "<blockquote>").replace("</think>", "</blockquote>")
    
    # Encabezado unificado para ambos modos
    LLM_HEADER = f"<b>{alias}{server_info} Reply</b> :\n"
    update_conversation_history(message.from_user.id, prompt, response_no_thinking, source)
    
    await message.reply(f"{LLM_HEADER}{response}")
    print(f"{LLM_HEADER}{response}")
    
async def query_api(prompt, api_name, message):
    """Centraliza las consultas a diferentes APIs"""
    api_url = config['api_endpoints'][api_name]
    
    if api_name.lower() == "deepseek":
        return await query_deepseek_api(prompt, message)
    elif api_name.lower() == "openai":
        return await query_openai_api(prompt, message)
    else:
        return f"⚠️ API no implementada: {api_name}", "API-Error"
    
async def chat_handler1(client, message):
    """Procesar mensajes privados."""
    if not await msg(client, message):
        return

    if message.text.startswith("/"):
        await processCommand(client, message)
        return

    prompt = message.text
    
    # Si el mensaje empieza con !api usa la API de DeepSeek
    if prompt.startswith("!api "):
        response, alias = await query_deepseek_api(prompt[5:], message)
    else:
        # Modo normal con LLM local
        response, alias = await query_ollama(prompt, client, message)
    
    response_no_thinking = remove_thinking_tags(response)
    response = response.replace("<think>", "<blockquote>").replace("</think>", "</blockquote>")
    
    LLM_HEADER = f"<b>{alias}[{SERVER_INDEX}] Reply</b> :\n"
    update_conversation_history(message.from_user.id, prompt, response_no_thinking)
    
    await message.reply(f"{LLM_HEADER}{response}")
    
async def chat_handler1(client, message):
    """Procesar mensajes privados."""
    print(f"________________________________________________________________________________")
    print(f"user={message.from_user.username}: {message.text}")
    print(f"____________________")
    if not await msg(client, message):
        return

    if message.text.startswith("/"):
        await processCommand(client, message)
        return

    prompt = message.text
    response, alias = await query_ollama(prompt, client, message)
    #response = re.sub(r"<think>(.*?)</think>", r"<i>\1</i>", response) 
    response_no_thninking=remove_thinking_tags(response)
    response = response.replace("<think>", "<blockquote>").replace("</think>", "</blockquote>")
    print (f"new response={response}")
    LLM_HEADER = f"<b>{alias}[{SERVER_INDEX}] Reply</b> :\n"
    
    # Guardar en el historial
    update_conversation_history(message.from_user.id, prompt, response_no_thninking)
    
    await message.reply(f"{LLM_HEADER}{response}")
    print(f"{LLM_HEADER}{response}")

async def msg(client, message):
    msgNotAllowed = "You're not authorized to use this bot."
    if message.from_user.username not in config["authorized_users"] and config["authorized_users"][0]!="*":
        await message.reply(msgNotAllowed)
        print(msgNotAllowed)
        return False
    return True


async def processCommand(client, message):
    global OLLAMA_CONTEXT, OLLAMA_TEMPERATURE, USE_API_MODE, CURRENT_API, SERVER_INDEX, MAX_HISTORY, CONVERSATION_HISTORY
    
    if message.text == "/help": #====================================================== HELP
        msg   =  "Comandos disponibles:"
        msg += "\n/help - Mostrar ayuda!"
        msg += "\n/context - Ver o cambiar el contexto"
        msg += "\n/temperature - Ver o cambiar la temperatura"
        msg += "\n/server - Ver o cambiar el servidor actual"
        msg += "\n/reload - recargar la configuración del bot"
        msg += "\n/list - lista de servidores incluyendo status"
        msg += "\n/qlist - lista de servidores sin incluír status"
        msg += "\n/historymax - especifica cuantos mensajes máximo tiene el historial del chat para el bot. Es la memoria 'persistente' para el bot."
        msg += "\n/historylist - lista los últimos 10 mensajes del historial"
        msg += "\n/clearmem - Borra el historial de conversación y comienza de nuevo"
        msg += "\n/filelist - Lista los ficheros cargados en el contexto"
        msg += "\n/selectfile_[index] - Selecciona el fichero de contexto (se obtiene con filelist)"
        msg += "\n//removefile_[idx] - Elimina el fichero del contexto (se obtiene con filelist)"
        msg += "\n/summary - crea un resumen del fichero seleccionado."
        msg += "\n/clearfiles - Elimina todos los ficheros de contexto"
        
        #await message.reply("Comandos disponibles:\n/help - Mostrar ayuda\n/context - Ver o cambiar el contexto\n/temperature - Ver o cambiar la temperatura\n/server - Ver o cambiar el servidor actual")
        await message.reply(msg)
    
    elif message.text.startswith("/temperature"):  #=================================== TEMPERATURE
        try:
            _, new_temp = message.text.split(" ", 1)
            new_temp = float(new_temp)
            if 0.0 <= new_temp <= 1.0:
                OLLAMA_TEMPERATURE = new_temp
                await message.reply(f"Temperatura establecida en {OLLAMA_TEMPERATURE}")
                update_config("temperature",OLLAMA_TEMPERATURE)
            else:
                await message.reply("Error: La temperatura debe estar entre 0.0 y 1.0.")
        except:
            await message.reply(f"Temperatura actual: {OLLAMA_TEMPERATURE}\nPara configurar: /temperature [0.0 - 1.0]")

    elif message.text.startswith("/context"):  #======================================= CONTEXT
        try:
            _, new_context = message.text.split(" ", 1)
            OLLAMA_CONTEXT = new_context
            await message.reply(f"Contexto actualizado:\n{OLLAMA_CONTEXT}")
            update_config("context",OLLAMA_CONTEXT)
        except:
            await message.reply(f"Contexto actual: {OLLAMA_CONTEXT}\nPara configurar: /context [nuevo contexto]")
    
    elif message.text.startswith("/server"):  #======================================== SERVER
        try:
            USE_API_MODE = False  # Volver al modo servidor
            command_text = message.text.replace("_", " ")  # Reemplaza "_" por " "
            _, new_index = command_text.split(" ", 1)
            new_index = int(new_index)
            if 0 <= new_index < len(OLLAMA_SERVERS):
                SERVER_INDEX = new_index
                await message.reply(f"Servidor cambiado al índice {SERVER_INDEX}\n{OLLAMA_SERVERS[SERVER_INDEX][2]}")
                update_config("server_index", SERVER_INDEX)
            else:
                await message.reply("Error: Índice fuera de rango.")
        except:
            await message.reply(f"Servidor actual: {SERVER_INDEX}\n{OLLAMA_SERVERS[SERVER_INDEX][2]}\nPara cambiar: /server [índice]")

    elif message.text.startswith("/reload"):  #======================================== RELOAD
        load_config()
        msg = "Configuración cargada..."
        await message.reply(msg)
        print(msg)
        
    elif message.text == "/qlist":  #=================================================== LIST
        servers_list = "Lista de servidores:\n"
        for index, (host, port, alias, model) in enumerate(OLLAMA_SERVERS):
            servers_list += f"/server_{index} - {alias}\n"
        servers_list += f"\nServidor actual: {SERVER_INDEX}\n{OLLAMA_SERVERS[SERVER_INDEX][2]}"
        await message.reply(servers_list)
        print(servers_list)

    elif message.text == "/status_model0":  #=================================================== LIST
        servers_list = "📋 *Lista de servidores*:\n\n"

        # Verificar estado de cada servidor
        for index, (host, port, alias, model) in enumerate(OLLAMA_SERVERS):
            status = await check_server_health(host, port, full_check=False)
            status_icon = {
                "online": "🟢",
                "online_no_service": "🟡",
                "offline": "🔴"
            }.get(status, "⚪")

            # Intentar obtener modelo actual solo si está online
            model_info = "Error al consultar"
            if status == "online":
                try:
                    url = f"http://{host}:{port}/api/show"
                    response = requests.get(url, timeout=5)
                    if response.status_code == 200:
                        data = response.json()
                        model_info = data.get("model", "Sin modelo")
                    else:
                        model_info = "Sin respuesta"
                except Exception:
                    model_info = "Error al consultar"
            else:
                model_info = "(sin conexión)"

            servers_list += f"{status_icon} /server_{index} - {alias} - Modelo: {model_info}\n"

        # Información del servidor actual
        current_host, current_port, current_alias, _ = OLLAMA_SERVERS[SERVER_INDEX]
        current_status = await check_server_health(current_host, current_port, full_check=True)

        # Modelo actual del servidor activo
        if current_status == "online":
            try:
                url = f"http://{current_host}:{current_port}/api/show"
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    current_model = data.get("model", "Sin modelo")
                else:
                    current_model = "Sin respuesta"
            except Exception:
                current_model = "Error al consultar"
        else:
            current_model = "(no disponible)"

        status_text = {
            "online": "🟢 Operativo",
            "online_no_service": "🟡 Servidor activo pero Ollama no responde",
            "offline": "🔴 Inaccesible"
        }.get(current_status, "⚪ Estado desconocido")

        servers_list += f"\n*Servidor actual*: {SERVER_INDEX}\n"
        servers_list += f"🔹 {current_alias}\n"
        servers_list += f"📊 Estado: {status_text}\n"
        servers_list += f"🔧 Modelo: {current_model}"

        await message.reply(servers_list)
        print(servers_list)


    elif message.text == "/status_models2":  #=================================================== LIST
        servers_list = "📋 *Lista de servidores*:\n\n"
        
        # Verificar estado de cada servidor
        for index, (host, port, alias, model) in enumerate(OLLAMA_SERVERS):
            full = (index == SERVER_INDEX)  # Solo el servidor actual se verifica con full_check=True
            status = await check_server_health(host, port, full_check=full)
            status_icon = {
                "online": "🟢",
                "online_no_service": "🟡",
                "offline": "🔴"
            }.get(status, "⚪")
            
            # Verificar el modelo cargado
            model_status = await get_model_status(host, port)
            
            servers_list += f"{status_icon} /server_{index} - {alias} - Modelo: {model_status}\n"
        
        # Obtener información del servidor actual
        current_host, current_port, current_alias, _ = OLLAMA_SERVERS[SERVER_INDEX]
        current_status = await check_server_health(current_host, current_port, full_check=True)  # Solo para el servidor actual
        
        # Obtener el modelo cargado
        current_model_status = await get_model_status(current_host, current_port)
        
        status_text = {
            "online": "🟢 Operativo",
            "online_no_service": "🟡 Servidor activo pero Ollama no responde",
            "offline": "🔴 Inaccesible"
        }.get(current_status, "⚪ Estado desconocido")
        
        servers_list += f"\n*Servidor actual*: {SERVER_INDEX}\n"
        servers_list += f"🔹 {current_alias}\n"
        servers_list += f"📊 Estado: {status_text}\n"
        servers_list += f"🔧 Modelo: {current_model_status}"
        
        await message.reply(servers_list)
        print(servers_list)


    elif message.text == "/status_models1":  #=================================================== LIST
        servers_list = "📋 *Lista de servidores*:\n\n"
        
        # Verificar estado de cada servidor
        for index, (host, port, alias, model) in enumerate(OLLAMA_SERVERS):
            full = (index == SERVER_INDEX)  # Solo el servidor actual se verifica con full_check=True
            status = await check_server_health(host, port, full_check=full)
            status_icon = {
                "online": "🟢",
                "online_no_service": "🟡",
                "offline": "🔴"
            }.get(status, "⚪")
            
            servers_list += f"{status_icon} /server_{index} - {alias}\n"
        
        # Obtener información del servidor actual
        current_host, current_port, current_alias, _ = OLLAMA_SERVERS[SERVER_INDEX]
        current_status = await check_server_health(current_host, current_port, full_check=True)  # Solo para el servidor actual
        
        status_text = {
            "online": "🟢 Operativo",
            "online_no_service": "🟡 Servidor activo pero Ollama no responde",
            "offline": "🔴 Inaccesible"
        }.get(current_status, "⚪ Estado desconocido")
        
        servers_list += f"\n*Servidor actual*: {SERVER_INDEX}\n"
        servers_list += f"🔹 {current_alias}\n"
        servers_list += f"📊 Estado: {status_text}"
        
        await message.reply(servers_list)
        print(servers_list)
    
    elif message.text == "/list":  #=================================================== LIST
        servers_list = "📋 *Lista de servidores*:\n\n"
        
        # Verificar estado de cada servidor
        for index, (host, port, alias, model) in enumerate(OLLAMA_SERVERS):
            status = await check_server_health(host, port, full_check=False)
            status_icon = {
                "online": "🟢",
                "online_no_service": "🟡",
                "offline": "🔴"
            }.get(status, "⚪")
            
            servers_list += f"{status_icon} /server_{index} - {alias}\n"
        
        # Añadir información del servidor actual
        current_host, current_port, current_alias, _ = OLLAMA_SERVERS[SERVER_INDEX]
        current_status = await check_server_health(current_host, current_port, full_check=True)
        
        status_text = {
            "online": "🟢 Operativo",
            "online_no_service": "🟡 Servidor activo pero Ollama no responde",
            "offline": "🔴 Inaccesible"
        }.get(current_status, "⚪ Estado desconocido")
        
        servers_list += f"\n*Servidor actual*: {SERVER_INDEX}\n"
        servers_list += f"🔹 {current_alias}\n"
        servers_list += f"📊 Estado: {status_text}"
        
        await message.reply(servers_list)
        print(servers_list)

    elif message.text == "/tcpconn":
        for host, port, alias, _ in OLLAMA_SERVERS:
            try:
                # Usamos socket directamente para mayor control
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)  # Timeout reducido
                result = sock.connect_ex((host, port))
                sock.close()
                return result == 0  # 0 = éxito
            except Exception as e:
                print(f"⚠️ Error en test_tcp_connection({host}:{port}): {str(e)}")
                return False

        
    elif message.text.startswith ("/historymax"):  #===================================== HISTORYMAX
        try:
            _, new_index = message.text.split(" ", 1)
            new_index = int(new_index)
            if 0 <= new_index < 100:
                MAX_HISTORY = new_index
                await message.reply(f"Se ha configurado el máximo del hisorial en {new_index} mensajes")
                update_config("max_history",MAX_HISTORY)
            else:
                await message.reply("Error: Índice fuera de rango.")
        except:
            await message.reply(f"El máximo del hisorial actual es: {MAX_HISTORY} mensajes")

    elif message.text.startswith("/historylist"):
        user_id = message.from_user.id
        CONVERSATION_HISTORY[user_id] = load_conversation_history(user_id)
        if user_id in CONVERSATION_HISTORY and CONVERSATION_HISTORY[user_id]:
            history_text = "\n".join(
                f"[{i+1}] {msg}" 
                for i, msg in enumerate(CONVERSATION_HISTORY[user_id][-10:])
            )
            history_text = truncate_message(history_text)
            await message.reply(f"Historial reciente (últimos 10 mensajes):\n{history_text}")
        else:
            await message.reply("No hay historial disponible.")
        
    elif message.text == "/clearmem":  #============================================== CLEARMEM
        user_id = message.from_user.id
        clear_conversation_history(user_id)
        await message.reply("Historial de conversación borrado. Comenzando de nuevo.")

    elif message.text == "/apimode":
        """Cambia entre modo local y API"""
        global USE_API
        USE_API = not USE_API
        mode = "API" if USE_API else "Local"
        await message.reply(f"Modo cambiado a: {mode}")

    
    #elif message.text == "/apis":
    #    """Lista las APIs disponibles"""
    #    apis = "\n".join([f"{name}: {url}" for name, url in config['api_endpoints'].items()])
    #    await message.reply(f"APIs disponibles:\n{apis}")
        
    elif message.text.startswith("/api_"):
        try:
            api_index = int(message.text.split("_")[1])
            valid_apis = [
                name for name, key in config['api_keys'].items() 
                if key and key.strip() not in ["tu_key"]
            ]
            api_name = valid_apis[api_index]
            USE_API_MODE = True
            CURRENT_API = api_name
            await message.reply(f"🔌 Modo API activado: {api_name.capitalize()}")
        except (IndexError, ValueError):
            await message.reply("⚠️ Índice de API inválido o sin key configurada")
    
    elif message.text == "/apis":
        await list_apis(message)
        
    elif message.text.startswith("/selectfile_"):
        file_index = int(message.text.split("_")[1])
        await selectfile(client, message, file_index)
    
    elif message.text.startswith("/removefile_"):
        file_index = int(message.text.split("_")[1])
        await removefile(client, message, file_index)
    
    elif message.text == "/filelist":
        await filelist(message)
    
    elif message.text == "/clearfiles":
        await clearfiles(message)
    
    elif message.text == "/disablefile":
        user_id = message.from_user.id
        if user_id in USER_FILES:
            USER_FILES[user_id]["current_file"] = None
        await message.reply("🗑️ Contexto de archivo desactivado")
        
    elif message.text == "/summary":
        await generate_summary(client, message)
    
def truncate_message(message, limit=2048):
    """Trunca el mensaje manteniendo solo los últimos 'limit' caracteres."""
    return message[-limit:] if len(message) > limit else message

def remove_thinking_tags(chat_history):
    """Elimina los tags <thinking> y su contenido del texto."""
    return re.sub(r"<think>.*?</think>", "", chat_history, flags=re.DOTALL).strip()

def clear_conversation_history(user_id):
    """Elimina el historial de conversación de un usuario."""
    global CONVERSATION_HISTORY
    # Eliminar de memoria
    if user_id in CONVERSATION_HISTORY:
        del CONVERSATION_HISTORY[user_id]
    
    # Eliminar archivo si existe
    file_path = f"{user_id}.json"
    if os.path.exists(file_path):
        os.remove(file_path)

async def query_deepseek_api(prompt, message):
    """Envía el mensaje a la API de DeepSeek"""
    user_id = message.from_user.id
    headers = {
        "Authorization": f"Bearer {config['api_keys']['deepseek']}",
        "Content-Type": "application/json"
    }
    
    # Incorporamos el historial en el formato que espera DeepSeek
    messages = [{"role": "system", "content": OLLAMA_CONTEXT}]
    
    if user_id in CONVERSATION_HISTORY:
        # Procesamos el historial para adaptarlo al formato de la API
        for msg in CONVERSATION_HISTORY[user_id]:
            if msg.startswith("User:"):
                messages.append({"role": "user", "content": msg[6:]})
            elif msg.startswith("Bot["):
                messages.append({"role": "assistant", "content": msg.split("]: ")[1]})
    
    messages.append({"role": "user", "content": prompt})
    
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": OLLAMA_TEMPERATURE
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(config['api_endpoints']['deepseek'], 
                                 headers=headers, 
                                 json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['choices'][0]['message']['content'], "DeepSeek"
                else:
                    error = await response.text()
                    raise Exception(f"API Error {response.status}: {error}")
    except Exception as e:
        print(f"Error con DeepSeek API: {e}")
        return f"Error al conectar con DeepSeek: {str(e)}", "DeepSeek-Error"


async def query_openai_api(prompt, message):
    """Envía el mensaje a la API de OpenAI"""
    headers = {
        "Authorization": f"Bearer {config['api_keys']['openai']}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "gpt-4",
        "messages": [
            {"role": "system", "content": OLLAMA_CONTEXT},
            {"role": "user", "content": prompt}
        ],
        "temperature": OLLAMA_TEMPERATURE
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(config['api_endpoints']['deepseek'], 
                                 headers=headers, 
                                 json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['choices'][0]['message']['content'], "DeepSeek-API"
                else:
                    error = await response.text()
                    raise Exception(f"API Error {response.status}: {error}")
    except Exception as e:
        print(f"Error con DeepSeek API: {e}")
        return f"Error al conectar con DeepSeek: {str(e)}", "DeepSeek-Error"

# ======== Función pública: Verifica el estado de un servidor Ollama (TCP + API)
async def check_server_health(host: str, port: int, full_check: bool = False) -> str:
    """
    Verifica el estado de un servidor Ollama en dos niveles.
    Parámetros:
    host (str): Dirección IP/hostname del servidor
    port (int): Puerto del servicio Ollama
    full_check (bool): Si True, verifica también el endpoint de la API
    Retorna:
    str: Estado del servidor:
    - "offline": Servidor no accesible (nivel TCP)
    - "online_no_service": Servidor accesible pero Ollama no responde
    - "online": Servidor y Ollama operativos
    """
    # -------- Nivel 1: Verificación TCP básica
    try:
        reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=1.0
        )
        writer.close()
        await writer.wait_closed()
    except (socket.gaierror, ConnectionRefusedError, asyncio.TimeoutError):
        return "offline"
    # -------- Nivel 2: Verificación API Ollama (opcional)
    if full_check:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://{host}:{port}/api/tags",
                    timeout=1.5
                ) as response:
                    if response.status == 200:
                        return "online"
                    return "online_no_service"
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return "online_no_service"

    return "online"  # Si solo se hizo check TCP y pasó

async def test_tcp_connection(host, port):
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=1.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except:
        return False
        
async def test_ollama_api(host, port):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{host}:{port}/api/tags",
                timeout=1.5
            ) as response:
                return response.status == 200
    except:
        return False



def ping_host(host, timeout=2):
    system = platform.system().lower()
    if system == "windows":
        command = ["ping", "-n", "1", "-w", str(timeout * 1000), host]
    else:
        command = ["ping", "-c", "1", "-W", str(timeout), host]

    print(f"Pinging {host}...", end="")
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            print("ok!")
            return True
        else:
            print("no responde!")
            return False
    except Exception as e:
        print(f"Error: {e}")
        return False


async def test_tcp_connection(host, port):
    """Versión con reintentos para redes inestables"""
    for _ in range(2):  # 2 intentos
        #print(f"Testing {host}:{port}")
        print(f"Testing {host}:{port}...", end="")
        try:
            with socket.socket() as s:
                s.settimeout(5)
                s.connect((host, port))
                print ("ok!")
                return True
        except (socket.timeout, ConnectionRefusedError):
            print ("timeout!")
            continue
        except Exception as e:
            print(f"TCP Check Error: {type(e).__name__} - {e}")
    return False

async def get_model_status(host, port):
    """Verifica qué modelo está cargado en el servidor Ollama"""
    try:
        url = f"http://{host}:{port}/api/model"  # Endpoint hipotético para obtener el modelo cargado
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            model = data.get("model", "Ninguno")
            if model == "None" or model == "waiting":
                return "Esperando modelo"
            else:
                return model
        else:
            return "Error al consultar"
    except Exception as e:
        return f"Error: {str(e)}"

# ======== Función pública: Lista APIs disponibles
async def list_apis(message):
    """Muestra solo las APIs con keys configuradas"""
    global USE_API_MODE, CURRENT_API
    
    if not config.get('api_keys'):
        await message.reply("⚠️ No hay API keys configuradas en config.json")
        return

    api_list = "🔑 *APIs disponibles* (con keys válidas):\n\n"
    available_apis = 0
    
    for index, (api_name, api_key) in enumerate(config['api_keys'].items()):
        if api_key and api_key.strip() not in ["", "tu_key"]:  # Filtra keys vacías/placeholders
            status = "✅ (Actual)" if USE_API_MODE and CURRENT_API == api_name else ""
            api_list += f"/api_{index} - {api_name.capitalize()} {status}\n"
            available_apis += 1
    
    if available_apis == 0:
        await message.reply("⚠️ No hay APIs con keys válidas configuradas")
        return
    
    api_list += f"\nModo actual: {'🔌 API' if USE_API_MODE else '🖥️ Servidor'}"
    await message.reply(api_list)

def get_loaded_model():
    try:
        output = subprocess.check_output(["ollama", "ps"], stderr=subprocess.DEVNULL, text=True)
        lines = output.strip().splitlines()
        if len(lines) > 1:
            model_name = lines[1].split()[0]  # Primera columna después del encabezado
            return model_name
        else:
            return "Sin modelo"
    except Exception as e:
        return f"Error al consultar: {e}"

# ======== Función pública: Subir archivo
@bot.on_message(filters.document)
async def addfile(client, message):
    user_id = message.from_user.id
    if not await msg(client, message):
        return

    try:
        file_path = await message.download()
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        if user_id not in USER_FILES:
            USER_FILES[user_id] = {"files": {}, "current_file": None}
        
        file_id = str(message.document.file_id)
        chunks = [content[i:i+CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE)]
        
        USER_FILES[user_id]["files"][file_id] = {
            "name": message.document.file_name,
            "chunks": chunks,
            "size": len(content)
        }
        
        await message.reply(
            f"📁 Archivo añadido: {message.document.file_name}\n"
            f"Chunks: {len(chunks)} | Tamaño: {len(content)//1024}KB\n"
            f"Usa /selectfile_{len(USER_FILES[user_id]['files'])-1} para seleccionarlo"
        )
    except Exception as e:
        await message.reply(f"❌ Error al procesar archivo: {str(e)}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# ======== Función pública: Listar archivos
async def filelist(message):
    user_id = message.from_user.id
    if user_id not in USER_FILES or not USER_FILES[user_id]["files"]:
        await message.reply("No tienes archivos en contexto")
        return
    
    files_list = "📂 *Archivos en contexto*:\n\n"
    for idx, (file_id, file_data) in enumerate(USER_FILES[user_id]["files"].items()):
        current_flag = " (🟢 Actual)" if file_id == USER_FILES[user_id]["current_file"] else ""
        files_list += (
            f"{idx}. {file_data['name']}{current_flag}\n"
            f"   Chunks: {len(file_data['chunks'])} | "
            f"/selectfile_{idx} | /removefile_{idx}\n\n"
        )
    
    files_list += f"\nUsa /clearfiles para borrar todos"
    await message.reply(files_list)

# ======== Función pública: Seleccionar archivo actual
async def selectfile(client, message, file_index):
    user_id = message.from_user.id
    try:
        file_id = list(USER_FILES[user_id]["files"].keys())[file_index]
        USER_FILES[user_id]["current_file"] = file_id
        file_name = USER_FILES[user_id]["files"][file_id]["name"]
        await message.reply(f"🟢 Archivo actual: {file_name}")
    except (IndexError, KeyError):
        await message.reply("❌ Índice de archivo inválido")

# ======== Función pública: Eliminar archivo
async def removefile(client, message, file_index):
    user_id = message.from_user.id
    try:
        file_id = list(USER_FILES[user_id]["files"].keys())[file_index]
        file_name = USER_FILES[user_id]["files"][file_id]["name"]
        
        if USER_FILES[user_id]["current_file"] == file_id:
            USER_FILES[user_id]["current_file"] = None
        
        del USER_FILES[user_id]["files"][file_id]
        await message.reply(f"🗑️ Archivo eliminado: {file_name}")
    except (IndexError, KeyError):
        await message.reply("❌ Índice de archivo inválido")

# ======== Función pública: Limpiar todos los archivos
async def clearfiles(message):
    user_id = message.from_user.id
    USER_FILES[user_id] = {"files": {}, "current_file": None}
    await message.reply("🧹 Todos los archivos eliminados del contexto")


def build_context(user_id, prompt):
    """Construye contexto con instrucciones + archivo + historial"""
    context_parts = []
    
    # 1. INSTRUCCIONES BASE (OLLAMA_CONTEXT)
    context_parts.append(f"🎯 INSTRUCCIONES PARA EL MODELO:\n{OLLAMA_CONTEXT}\n")
    
    # 2. ARCHIVO ACTUAL (si existe)
    if user_id in USER_FILES and USER_FILES[user_id]["current_file"]:
        file_data = USER_FILES[user_id]["files"][USER_FILES[user_id]["current_file"]]
        context_parts.append(f"\n📁 ATTACHED_FILE: {file_data['name']}\n")
        context_parts.extend(file_data["chunks"])
        print(f"➕ Añadido archivo: {file_data['name']} ({len(file_data['chunks'])} chunks)")
    
    # 3. HISTORIAL (opcional)
    if user_id in CONVERSATION_HISTORY:
        history = "\n".join(CONVERSATION_HISTORY[user_id][-MAX_HISTORY:])
        context_parts.append(f"\n🗨️ HISTORIAL DE CHAT:\n{history}")
    
    return "\n\n".join(context_parts)  # Dobles saltos para mejor legibilidad

    
def build_context1(user_id, prompt):
    """Combina historial de chat y archivos seleccionados"""
    context_parts = []
    print ("Adding chunks...")
    
    # 1. Historial de conversación
    if user_id in CONVERSATION_HISTORY:
        context_parts.append("\n".join(CONVERSATION_HISTORY[user_id][-MAX_HISTORY:]))
    
    # 2. Archivo actual (si está seleccionado)
    if user_id in USER_FILES and USER_FILES[user_id]["current_file"]:
        file_id = USER_FILES[user_id]["current_file"]
        file_data = USER_FILES[user_id]["files"][file_id]
        context_parts.append(f"\n[ARCHIVO: {file_data['name']}]\n")
        
        # Añadir solo el primer chunk inicialmente (podemos mejorarlo después)
        if file_data["chunks"]:
            context_parts.append(file_data["chunks"])
    
    return "\n".join(context_parts)

# ======== Función pública: Enviar mensajes largos segmentados
async def send_long_message(client, chat_id, text):
    """Divide mensajes largos respetando saltos de línea"""
    messages = []
    current_msg = ""
    
    for paragraph in text.split('\n'):
        if len(current_msg) + len(paragraph) + 1 > MAX_TELEGRAM_MSG:
            messages.append(current_msg)
            current_msg = paragraph
        else:
            current_msg += "\n" + paragraph
    
    if current_msg:
        messages.append(current_msg)
    
    for msg in messages:
        await client.send_message(chat_id, msg)
        await asyncio.sleep(0.5)  # Evita flooding

# ======== Versión mejorada de generate_summary
async def generate_summary(client, message):
    user_id = message.from_user.id
    
    if user_id not in USER_FILES or not USER_FILES[user_id]["current_file"]:
        await message.reply("⚠️ No hay archivo seleccionado. Usa /selectfile primero")
        return

    file_data = USER_FILES[user_id]["files"][USER_FILES[user_id]["current_file"]]
    file_content = "\n".join(file_data["chunks"])
    
    await client.send_chat_action(message.chat.id, ChatAction.TYPING)
    
    # Optimización: Usar solo los primeros 20K caracteres para análisis
    analysis_content = file_content[:20000]
    file_ext = file_data["name"].split('.')[-1].lower()
    
    try:
        # Construir prompt eficiente
        prompt = (
            f"Genera resumen conciso de {file_data['name']} con:\n"
            f"1. Propósito (1 línea)\n"
            f"2. Elementos clave (máx 5 ítems)\n"
            f"3. Problemas/sugerencias (máx 3)\n"
            f"Contenido:\n```\n{analysis_content}\n```"
        )
        
        if USE_API_MODE:
            summary, _ = await query_api(prompt, CURRENT_API, message)
        else:
            summary, _ = await query_ollama(prompt, client, message)
        
        formatted_summary = (
            summary.replace("<think>", "<blockquote>")
                  .replace("</think>", "</blockquote>")
        )

        # Encabezado común para todos los chunks
        header = (
            f"📝 **Resumen de {file_data['name']}**\n\n"
            f"🗂️ **Tipo**: {file_ext.upper()}\n"
            f"📏 **Tamaño**: {len(file_content)//1024} KB\n"
            f"🔍 **Análisis generado por**: {CURRENT_API if USE_API_MODE else 'Ollama'}\n\n"
        )
        
        # Enviar en partes
        full_response = header + formatted_summary
        await send_long_message(client, message.chat.id, full_response)
        
    except Exception as e:
        await message.reply(f"❌ Error al generar resumen: {str(e)}")

# ======== Método auxiliar: Dividir texto preservando párrafos
def split_preserving_paragraphs(text, max_length=MAX_TELEGRAM_MSG):
    """Divide texto respetando saltos de línea naturales"""
    parts = []
    while len(text) > max_length:
        split_at = text.rfind('\n', 0, max_length)
        if split_at == -1:
            split_at = max_length
        parts.append(text[:split_at])
        text = text[split_at:].lstrip('\n')
    parts.append(text)
    return parts
    
# ======== Método privado: Construir prompt de análisis
def build_analysis_prompt(content, file_ext):
    """Crea un prompt específico según el tipo de archivo"""
    base_instructions = {
        'py': "Analiza este script Python y genera:\n1. Propósito general\n2. Lista de funciones/clases\n3. Dependencias\n4. 3 sugerencias de mejora",
        'js': "Proporciona un resumen de este código JavaScript:\n1. Funcionalidad principal\n2. Métodos exportados\n3. Eventos importantes\n4. Problemas potenciales",
        'txt': "Resume este texto en 3 puntos clave manteniendo el contexto original:",
        'md': "Extrae los conceptos clave de este markdown y organiza la información jerárquicamente:",
        'default': "Genera un resumen estructurado de este archivo incluyendo:\n1. Tipo de contenido\n2. Temas principales\n3. Elementos notables"
    }
    
    prompt = base_instructions.get(file_ext, base_instructions['default'])
    return f"{prompt}\n\n```\n{content[:15000]}\n```"  # Limitar a ~15K caracteres

        
def main():
    print ("Bot initiated...")
    bot.run()
    
if __name__ == "__main__":
    asyncio.run(main())


#async def main():
#    print("Bot initiated...")
#    await bot.start()
#    await bot.send_message("Deen0X","Online")
#    await bot.run_until_disconnected()
#
#if __name__ == "__main__":
#    loop = asyncio.get_event_loop()
#    try:
#        loop.run_until_complete(main())
#    except KeyboardInterrupt:
#        print("Bot detenido manualmente.")
#    finally:
#        loop.run_until_complete(bot.stop())
#        loop.close()