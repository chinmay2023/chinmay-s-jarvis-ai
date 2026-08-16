import os
import re
import json
import base64
import asyncio
import requests
import edge_tts
import wikipediaapi
from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from openai import OpenAI
from duckduckgo_search import DDGS
from .models import ChatMessage, UserProfile, UserMemory, TaskItem

# Initialize Groq client
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

# Initialize Wikipedia API client
wiki = wikipediaapi.Wikipedia(
    language='en',
    user_agent='JarvisAI/2.0 (voice_assistant_project; contact@jarvisai.local)'
)

# Neural AI Voices
VOICE_ENGLISH = "en-US-ChristopherNeural"
VOICE_INDIAN = "hi-IN-MadhurNeural"       # Natural deep voice for Hindi & Hinglish
VOICE_MARATHI = "mr-IN-ManoharNeural"     # Native Marathi neural voice

# In-memory caches for zero-latency lookups
SALUTATION_CACHE = {}
WAKE_AUDIO_CACHE = {}


def detect_language_and_voice(text):
    """Dynamically chooses the correct voice model based on the language script or phonetic tokens."""
    if re.search(r'[\u0900-\u097F]', text):
        return VOICE_MARATHI, "-10Hz", "+3%"

    indian_cues = [
        "ahe", "kaay", "kasa", "aamchi", "bolu", "vichar", "theek", "hai", 
        "kya", "kaise", "bhai", "karo", "namaskar", "aapan", "sang", "aahe",
        "tuzi", "tuzhe", "mala", "tula", "karu", "karte", "zala", "kiti"
    ]
    words = text.lower().split()
    if any(cue in words for cue in indian_cues):
        return VOICE_INDIAN, "-10Hz", "+4%"

    return VOICE_ENGLISH, "-14Hz", "+4%"


async def generate_voice_base64(text):
    """Generates low-latency audio with automatic accent & language switching."""
    clean_text = re.sub(r'<function.*?</function>', '', text, flags=re.DOTALL)
    clean_text = re.sub(r'[{}\[\]*#`_~]', '', clean_text).strip()

    if not clean_text:
        return None

    voice, pitch, rate = detect_language_and_voice(clean_text)

    try:
        communicate = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
        audio_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
        return base64.b64encode(audio_data).decode('utf-8')
    except Exception as e:
        print(f"⚠️ Edge-TTS Error: {e}")
        return None


def get_user_salutation(user):
    """Retrieves salutation from profile or detects dynamically."""
    if hasattr(user, 'profile') and user.profile.gender:
        return "maam" if user.profile.gender == 'female' else "sir"

    clean_name = user.username.lower().strip()
    if clean_name in SALUTATION_CACHE:
        return SALUTATION_CACHE[clean_name]

    female_terms = ["girl", "woman", "female", "lady", "miss", "mrs", "ms"]
    if any(term in clean_name for term in female_terms):
        SALUTATION_CACHE[clean_name] = "maam"
        return "maam"

    try:
        prompt = f"Determine if the username/name '{user.username}' is typically Male or Female. Respond with ONLY 'sir' or 'maam'."
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=5
        )
        result = response.choices[0].message.content.strip().lower()
        salutation = "maam" if ("maam" in result or "female" in result or "ma'am" in result) else "sir"
        SALUTATION_CACHE[clean_name] = salutation
        return salutation
    except Exception:
        return "sir"


def execute_web_search(query, default_city="Wardha"):
    """Multi-source free knowledge engine: Open-Meteo Weather -> Wikipedia -> DuckDuckGo."""
    clean_q = query.lower().strip()

    # 1. Real-time Weather & Temperature (Open-Meteo: 100% Free & Unlimited)
    if any(w in clean_q for w in ["weather", "temperature", "mausam", "havaman"]):
        try:
            city = re.sub(r'(what is the|how is the|tell me the|current|today|in|weather|temperature|forecast|of|\?)', '', clean_q).strip()
            if not city:
                city = default_city

            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=en&format=json"
            geo_res = requests.get(geo_url, timeout=3).json()
            if geo_res.get('results'):
                loc = geo_res['results'][0]
                lat, lon, name, country = loc['latitude'], loc['longitude'], loc['name'], loc.get('country', '')
                weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
                w_data = requests.get(weather_url, timeout=3).json().get('current_weather', {})
                if w_data:
                    return f"Current live weather in {name}, {country}: Temperature is {w_data.get('temperature')}°C with wind speed around {w_data.get('windspeed')} km/h."
        except Exception as e:
            print(f"⚠️ Weather API Notice: {e}")

    # 2. Encyclopedic Facts & People (Wikipedia API: 100% Free & Unlimited)
    try:
        clean_topic = re.sub(r'^(who is|what is|tell me about|explain|define|where is)\s+', '', clean_q).strip('?')
        if len(clean_topic) > 2:
            page = wiki.page(clean_topic)
            if page.exists():
                sentences = page.summary.split('. ')[:3]
                return ". ".join(sentences) + "."
    except Exception as e:
        print(f"⚠️ Wikipedia Notice: {e}")

    # 3. Real-Time Web & News (DuckDuckGo: Free & No API Key)
    try:
        results = list(DDGS().text(query, max_results=3))
        if results:
            snippets = [f"- {r.get('title', '')}: {r.get('body', '')}" for r in results if r.get('body')]
            return "\n".join(snippets)
    except Exception as e:
        print(f"⚠️ Live search notice: {e}")

    return "Live web information is currently unavailable."


def handle_task_command(user, cmd):
    """Processes task commands: adding, viewing, or clearing tasks."""
    add_match = re.search(r'(?:add|create|remind me to)\s+(?:task\s+)?(.+)', cmd, re.IGNORECASE)
    if ("add task" in cmd or "create task" in cmd or "remind me to" in cmd) and add_match:
        task_title = add_match.group(1).replace("to my list", "").replace("to todo", "").strip()
        TaskItem.objects.create(user=user, title=task_title)
        return f"I have added '{task_title}' to your task list."

    if any(k in cmd for k in ["my tasks", "show tasks", "todo list", "what are my tasks", "view tasks"]):
        tasks = TaskItem.objects.filter(user=user, is_completed=False).order_by('-created_at')[:5]
        if not tasks:
            return "Your task list is currently clear."
        task_list = ", ".join([f"{i+1}. {t.title}" for i, t in enumerate(tasks)])
        return f"Here are your pending tasks: {task_list}."

    if any(k in cmd for k in ["clear all tasks", "delete all tasks", "complete all tasks"]):
        TaskItem.objects.filter(user=user).update(is_completed=True)
        return "All your tasks have been marked as completed."

    return None


def handle_memory_command(user, cmd):
    """Stores user facts across sessions."""
    mem_match = re.search(r'remember that (.+)', cmd, re.IGNORECASE)
    if mem_match:
        fact = mem_match.group(1).strip()
        UserMemory.objects.create(user=user, key="fact", value=fact)
        return "Understood. I have recorded that in my permanent memory."
    return None


# Groq Tool Definition
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_world_information",
            "description": "Searches for real-time live events, news, facts, definitions, weather, people, and global information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to look up on the web or encyclopedia."
                    }
                },
                "required": ["query"]
            }
        }
    }
]


def register_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            UserProfile.objects.get_or_create(user=user)
            return redirect('assistance:login')
    else:
        form = UserCreationForm()
    return render(request, 'assistance/register.html', {'form': form})


@login_required(login_url='assistance:login')
def chat_view(request):
    salutation = get_user_salutation(request.user)
    return render(request, 'assistance/chat.html', {'salutation': salutation})


@login_required(login_url='assistance:login')
def clear_history_api(request):
    if request.method == 'POST':
        ChatMessage.objects.filter(user=request.user).delete()
        return JsonResponse({'status': 'cleared'})
    return JsonResponse({'error': 'Invalid method'}, status=405)


@login_required(login_url='assistance:login')
def get_tasks_api(request):
    tasks = TaskItem.objects.filter(user=request.user).order_by('-created_at')
    task_data = [{'id': t.id, 'title': t.title, 'is_completed': t.is_completed} for t in tasks]
    return JsonResponse({'tasks': task_data})


@login_required(login_url='assistance:login')
def jarvis_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    user_message = request.POST.get('message', '')
    user_name = request.user.username
    
    if not user_message:
        return JsonResponse({'error': 'No audio input received'}, status=400)

    salutation = get_user_salutation(request.user)
    cmd = user_message.lower().strip()

    # Instant Pre-Cached Wake Path
    if cmd == "__wake_greeting__":
        wake_reply = f"Online and ready, {salutation}."
        if salutation not in WAKE_AUDIO_CACHE:
            WAKE_AUDIO_CACHE[salutation] = asyncio.run(generate_voice_base64(wake_reply))
        return JsonResponse({'reply': wake_reply, 'audio': WAKE_AUDIO_CACHE[salutation]})

    # Dynamic Salutation Override
    if "i am a girl" in cmd or "i am female" in cmd or "call me ma'am" in cmd or "call me mam" in cmd:
        salutation = "maam"
        SALUTATION_CACHE[user_name.lower().strip()] = "maam"
        if hasattr(request.user, 'profile'):
            request.user.profile.gender = 'female'
            request.user.profile.save()
    elif "i am a boy" in cmd or "i am male" in cmd or "call me sir" in cmd:
        salutation = "sir"
        SALUTATION_CACHE[user_name.lower().strip()] = "sir"
        if hasattr(request.user, 'profile'):
            request.user.profile.gender = 'male'
            request.user.profile.save()

    # Fast Shutdown / Goodbye
    shutdown_keywords = ["goodbye", "good bye", "bye", "shutdown", "shut down", "go to sleep", "sleep"]
    if any(word in cmd for word in shutdown_keywords):
        reply = f"Shutting down systems. Have a good day, {salutation}."
        ChatMessage.objects.create(user=request.user, role='user', content=user_message)
        ChatMessage.objects.create(user=request.user, role='assistant', content=reply)
        audio_base64 = asyncio.run(generate_voice_base64(reply))
        return JsonResponse({'reply': reply, 'audio': audio_base64, 'shutdown': True})

    # Direct Task Handling
    task_response = handle_task_command(request.user, cmd)
    if task_response:
        ChatMessage.objects.create(user=request.user, role='user', content=user_message)
        ChatMessage.objects.create(user=request.user, role='assistant', content=task_response)
        audio_base64 = asyncio.run(generate_voice_base64(task_response))
        return JsonResponse({'reply': task_response, 'audio': audio_base64})

    # Direct Memory Handling
    memory_response = handle_memory_command(request.user, cmd)
    if memory_response:
        ChatMessage.objects.create(user=request.user, role='user', content=user_message)
        ChatMessage.objects.create(user=request.user, role='assistant', content=memory_response)
        audio_base64 = asyncio.run(generate_voice_base64(memory_response))
        return JsonResponse({'reply': memory_response, 'audio': audio_base64})

    # Save User Message
    ChatMessage.objects.create(user=request.user, role='user', content=user_message)

    # Fetch User Memories
    memories = UserMemory.objects.filter(user=request.user).order_by('-created_at')[:4]
    memory_context = "\n".join([f"- {m.value}" for m in memories]) if memories else "None"

    # Context Memory (last 4 turns)
    history = ChatMessage.objects.filter(user=request.user).order_by('-created_at')[:4]
    history_messages = [{"role": msg.role, "content": msg.content} for msg in reversed(history)]

    # Creator check
    is_creator_logged_in = user_name.lower().startswith("chinmay")
    creator_phrase = "you, Chinmay Pendke" if is_creator_logged_in else "Chinmay Pendke"

    other_salutation = "sir" if salutation == "maam" else "maam"

    system_instruction = {
        "role": "system",
        "content": (
            f"You are J.A.R.V.I.S., Tony Stark's highly intelligent AI assistant. "
            f"Address the user strictly as '{salutation}'. NEVER address them as '{other_salutation}'. "
            f"The user's registered name is '{user_name}'. If they ask for their name, answer: 'Your name is {user_name}, {salutation}.' "
            f"Known facts about this user:\n{memory_context}\n"
            f"You understand and reply fluently in English, Hindi, and Marathi based on what the user speaks. "
            f"Note: Input comes from speech-to-text transcriptions. Deduce true user intent accurately. "
            f"If asked 'Who created you?', state that your original concept stems from Tony Stark, but you were developed by {creator_phrase}. "
            f"NEVER output raw JSON, code blocks, or function XML tags like `<function>` in your final spoken reply. "
            f"Be conversational, natural, and punchy (1 to 2 sentences max). "
            f"Never use markdown formatting like asterisks (*), hashtags (#), or emojis."
        )
    }

    try:
        # Step 1: LLM Inference with Dynamic Search Tool
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[system_instruction] + history_messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=220
        )
        response_msg = response.choices[0].message

        # Step 2: Handle Autonomous World Knowledge Search Tool Call
        if response_msg.tool_calls:
            tool_messages = [system_instruction] + history_messages + [response_msg]
            default_city = request.user.profile.city if hasattr(request.user, 'profile') else "Wardha"
            
            for tool_call in response_msg.tool_calls:
                if tool_call.function.name == "search_world_information":
                    args = json.loads(tool_call.function.arguments)
                    search_query = args.get("query", user_message)
                    search_results = execute_web_search(search_query, default_city=default_city)

                    tool_messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": "search_world_information",
                        "content": search_results
                    })

            # Step 3: Synthesize Live Search Context into Voice Reply
            final_res = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=tool_messages,
                temperature=0.3,
                max_tokens=180
            )
            raw_reply = final_res.choices[0].message.content
        else:
            raw_reply = response_msg.content

        # Clean output
        reply = re.sub(r'<function.*?</function>', '', raw_reply, flags=re.DOTALL)
        reply = re.sub(r'[{}\[\]*#`_~]', '', reply).strip()

        ChatMessage.objects.create(user=request.user, role='assistant', content=reply)
        audio_base64 = asyncio.run(generate_voice_base64(reply))

        return JsonResponse({'reply': reply, 'audio': audio_base64})

    except Exception as e:
        print(f"❌ Groq API Error: {str(e)}")
        fallback_reply = f"My neural link experienced a slight glitch, {salutation}. Please try speaking again."
        audio_base64 = asyncio.run(generate_voice_base64(fallback_reply))
        return JsonResponse({'reply': fallback_reply, 'audio': audio_base64})