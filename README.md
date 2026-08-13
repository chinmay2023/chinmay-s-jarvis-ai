# J.A.R.V.I.S. Voice Assistant Web App

A sleek, Iron Man-inspired AI voice assistant web application built with **Django**, **Groq (Llama 3.3 70B)**, and real-time speech interaction capabilities.

Developed by **Chinmay Pendke**.

---

## Features

- **Interactive Core HUD:** Futuristic animated central node with dynamic visual states for Listening, Thinking, and Transmitting/Speaking.
- **Hands-Free Activation:** Supports single-click initialization and real-time **Double-Clap detection** using Web Audio API.
- **Fast Neural Responses:** Powered by `llama-3.3-70b-versatile` via Groq API for near-instant response generation.
- **Live Web Knowledge:** Integrated with DuckDuckGo real-time search context to answer current world queries.
- **Natural Voice Output:** Browser-native Text-to-Speech synthesis configured for crisp, spoken-friendly responses.
- **User Authentication:** Built-in Django authentication system with custom user registration and session history persistence.

---

## Tech Stack

- **Backend:** Python, Django
- **LLM Engine:** Groq API (`llama-3.3-70b-versatile`)
- **Live Search:** `duckduckgo-search`
- **Frontend:** HTML5, CSS3 (Keyframe Animations), JavaScript (Web Speech API, Web Audio API)
- **Database:** SQLite (Default local memory)

---

## Local Setup & Installation

### 1. Clone the Repository
```bash
git clone [https://github.com/chinmay2023/chinmay-s-jarvis-ai.git](https://github.com/chinmay2023/chinmay-s-jarvis-ai.git)
cd chinmay-s-jarvis-ai
