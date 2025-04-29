# TeleBotGPT
A simple bot for connect your OLLAMA Server and generate a ChatGPT bot on telegram

This program pretend to bring to you a basic structure for improve your own ChatGPT telegram BOT, using OLLAMA server running on local.

Features:
- Support for multiple OLLAMA servers. If one fails, then cycle test the next one until there is no more to test.
- Support for memory chat. you can configure how much messages the bot can remember. Use with caution.
- Telegram format message support for rich text reply.
- Formatted output for improve output messages.

Update 2025-04-29
- Support for API calling. You must configure you Api-Keys in config.json for use it.
- Support for upload files to context. You can setup a file to keep as part of the context and will send to bot on each call.
- Better detection of online services. Now the bot may take 2.5 seconds (timeout) to determine if server is online.
- Updated Server Lists command (/list). Included online status for each server.
- Updated /help command.

![image](https://github.com/user-attachments/assets/01de5f9a-372c-4556-b153-618f32346920)

Api list definde in config.json

![image](https://github.com/user-attachments/assets/20bdbfa2-7da1-4205-876d-8047652c57d3)

Updated Server Lists with Online Service Status



Availables commands:

/help - Mostrar ayuda

/context - Ver o cambiar el contexto

/temperature - Ver o cambiar la temperaturaç

/server - Ver o cambiar el servidor actual

![image](https://github.com/user-attachments/assets/8eeaeec3-a508-4bbf-9f53-0498946ccf7c)

List and selection of servers


![image](https://github.com/user-attachments/assets/008f1c2e-f314-4463-91a2-93a88cc7956a)

Think process formatted into Quote blocks.

Code formatted into Code blocks



Hope you found useful!
