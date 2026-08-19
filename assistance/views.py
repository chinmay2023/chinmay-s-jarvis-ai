# jarvis_web/assistance/views.py
import os
import re
import json
import base64
import asyncio
import requests
import edge_tts
import wikipediaapi
import concurrent.futures
from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.http import JsonResponse
from openai import OpenAI
from duckduckgo_search import DDGS

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None

from .forms import CustomUserCreationForm, detect_gender_from_name
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

# Initialize Tavily AI client (for full real-time world intelligence)
tavily_api_key = os.getenv("TAVILY_API_KEY")
tavily_client = TavilyClient(api_key=tavily_api_key) if (TavilyClient and tavily_api_key) else None

# Default Signature JARVIS Voice (English)
DEFAULT_VOICE = "en-US-ChristopherNeural"
DEFAULT_PITCH = "-14Hz"
DEFAULT_RATE = "+4%"

# Neural Voice Model Mapping for Indian Scripts
INDIAN_VOICE_MAP = [
    # Bengali
    (r'[\u0980-\u09FF]', 'bn-IN-BashkarNeural', '+0Hz', '+0%'),
    # Tamil
    (r'[\u0B80-\u0BFF]', 'ta-IN-ValluvarNeural', '+0Hz', '+0%'),
    # Telugu
    (r'[\u0C00-\u0C7F]', 'te-IN-MohanNeural', '+0Hz', '+0%'),
    # Kannada
    (r'[\u0C80-\u0CFF]', 'kn-IN-GaganNeural', '+0Hz', '+0%'),
    # Malayalam
    (r'[\u0D00-\u0D7F]', 'ml-IN-MidhunNeural', '+0Hz', '+0%'),
    # Gujarati
    (r'[\u0A80-\u0AFF]', 'gu-IN-NiranjanNeural', '+0Hz', '+0%'),
    # Punjabi (Gurmukhi)
    (r'[\u0A00-\u0A7F]', 'pa-IN-OjasNeural', '+0Hz', '+0%'),
    # Urdu (Perso-Arabic)
    (r'[\u0600-\u06FF]', 'ur-IN-SalmanNeural', '+0Hz', '+0%'),
    # Marathi / Hindi (Devanagari)
    (r'[\u0900-\u097F]', 'mr-IN-ManoharNeural', '+0Hz', '+0%'),
]

# In-memory cache for wake audio
WAKE_AUDIO_CACHE = {}

# ThreadPoolExecutor to prevent asyncio loop collisions across Django workers
AUDIO_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def get_voice_parameters(text):
    """Detects native script or phonetic cues and assigns the appropriate tuned neural voice."""
    for pattern, voice, pitch, rate in INDIAN_VOICE_MAP:
        if re.search(pattern, text):
            return voice, pitch, rate

    # Phonetic Latin cues for Marathi / Hindi
    clean_lower = text.lower()
    marathi_latin_cues = ["ahe", "kaay", "kasa", "kashi", "bolu", "vichar", "namaskar", "aapan", "sang", "aahe", "tuzi", "tuzhe", "mala", "tula", "karu", "karte", "zala", "zali", "kiti", "kuthe", "kashala", "teva"]
    words = clean_lower.split()
    if any(cue in words for cue in marathi_latin_cues):
        return 'mr-IN-ManoharNeural', '+0Hz', '+0%'

    hindi_latin_cues = ["hai", "kya", "kaise", "kaisa", "bhai", "karo", "theek", "bolo", "batao", "mujhe", "tum", "aap", "achha"]
    if any(cue in words for cue in hindi_latin_cues):
        return 'hi-IN-MadhurNeural', '+0Hz', '+0%'

    return DEFAULT_VOICE, DEFAULT_PITCH, DEFAULT_RATE


async def _synthesize_edge_tts(clean_text, voice, rate, pitch):
    """Internal coroutine for stream synthesis."""
    communicate = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
    return b"".join(audio_chunks) if audio_chunks else None


def generate_voice_base64_sync(text):
    """Safe thread-isolated runner that prevents event loop crashes on mobile and WSGI environments."""
    if not text:
        return None

    clean_text = re.sub(r'<function.*?</function>', '', text, flags=re.DOTALL)
    clean_text = re.sub(r'[{}\[\]*#`_~<>\\]', '', clean_text).strip()

    if not clean_text:
        return None

    voice, pitch, rate = get_voice_parameters(clean_text)

    def _run_in_new_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            raw_audio = loop.run_until_complete(_synthesize_edge_tts(clean_text, voice, rate, pitch))
            if raw_audio:
                return base64.b64encode(raw_audio).decode('utf-8')
            return None
        except Exception as e:
            print(f"⚠️ Audio Synthesis Warning: {e}")
            return None
        finally:
            loop.close()

    future = AUDIO_POOL.submit(_run_in_new_loop)
    return future.result()


def get_user_salutation(user):
    """Retrieves salutation from profile or dynamically detects it from username."""
    if hasattr(user, 'profile') and user.profile.gender:
        return "maam" if user.profile.gender == 'female' else "sir"

    detected = detect_gender_from_name(user.username)
    return "maam" if detected == "female" else "sir"


def execute_web_search(query, default_city="Wardha"):
    """Multi-tiered real-time global intelligence engine."""
    clean_q = query.lower().strip()

    # 1. Real-time Weather & Temperature (Open-Meteo)
    if any(w in clean_q for w in ["weather", "temperature", "mausam", "havaman", "forecast", "climate"]):
        try:
            city = re.sub(r'(what is the|how is the|tell me the|current|today|in|weather|temperature|forecast|of|\?)', '', clean_q).strip()
            if not city:
                city = default_city

            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=en&format=json"
            geo_res = requests.get(geo_url, timeout=4).json()
            if geo_res.get('results'):
                loc = geo_res['results'][0]
                lat, lon, name, country = loc['latitude'], loc['longitude'], loc['name'], loc.get('country', '')
                weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
                w_data = requests.get(weather_url, timeout=4).json().get('current_weather', {})
                if w_data:
                    return f"Current live weather in {name}, {country}: Temperature is {w_data.get('temperature')}°C with wind speed around {w_data.get('windspeed')} km/h."
        except Exception as e:
            print(f"⚠️ Weather API Notice: {e}")

    # 2. Tavily AI Real-Time Web Engine (Fast & Accurate)
    if tavily_client:
        try:
            search_context = tavily_client.get_search_context(
                query=query,
                search_depth="basic",
                max_results=4
            )
            if search_context:
                return search_context
        except Exception as e:
            print(f"⚠️ Tavily Search Notice: {e}")

    # 3. DuckDuckGo Fallback Search
    try:
        results = list(DDGS().text(query, max_results=4))
        if results:
            snippets = [f"- {r.get('title', '')}: {r.get('body', '')}" for r in results if r.get('body')]
            return "\n".join(snippets)
    except Exception as e:
        print(f"⚠️ DuckDuckGo Fallback Notice: {e}")

    # 4. Wikipedia Encyclopedic Knowledge Fallback
    try:
        clean_topic = re.sub(r'^(who is|what is|tell me about|explain|define|where is|founder of)\s+', '', clean_q).strip('?')
        if len(clean_topic) > 2:
            page = wiki.page(clean_topic)
            if page.exists():
                sentences = page.summary.split('. ')[:3]
                return ". ".join(sentences) + "."
    except Exception as e:
        print(f"⚠️ Wikipedia Notice: {e}")

    return "No verified real-time information found on this topic."


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


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_world_information",
            "description": "Searches the live internet for current events, news, live sports, real-time weather, stock prices, definitions, facts, people, or any world knowledge outside LLM training cutoff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The exact search query to look up on the live web."
                    }
                },
                "required": ["query"]
            }
        }
    }
]


@ensure_csrf_cookie
def login_view(request):
    """Handles user login."""
    if request.user.is_authenticated:
        return redirect('assistance:chat')

    error_message = None

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('assistance:chat')
        else:
            error_message = "Invalid username or password."
    else:
        form = AuthenticationForm()

    return render(request, 'assistance/login.html', {'form': form, 'error_message': error_message})


@ensure_csrf_cookie
def register_view(request):
    """Registers standard users and auto-creates their profile with dynamic gender detection."""
    if request.user.is_authenticated:
        return redirect('assistance:chat')

    error_message = None

    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('assistance:login')
        else:
            error_message = form.errors
    else:
        form = CustomUserCreationForm()

    return render(request, 'assistance/register.html', {'form': form, 'error_message': error_message})


@csrf_exempt
def logout_view(request):
    """Safely terminates user session across both GET and POST requests."""
    auth_logout(request)
    response = redirect('assistance:login')
    response.delete_cookie('sessionid', path='/')
    return response


@ensure_csrf_cookie
def chat_view(request):
    """Voice HUD interface."""
    if not request.user.is_authenticated:
        return redirect('assistance:login')

    if not User.objects.filter(id=request.user.id).exists():
        auth_logout(request)
        return redirect('assistance:login')

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
    """Voice assistant API endpoint with thread-isolated neural TTS."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    if not User.objects.filter(id=request.user.id).exists():
        return JsonResponse({'error': 'Session expired. Please log in again.'}, status=401)

    user_message = request.POST.get('message', '').strip()
    user_name = request.user.username

    if not user_message:
        return JsonResponse({'error': 'No audio input received'}, status=400)

    salutation = get_user_salutation(request.user)
    cmd = user_message.lower()

    # Pre-Cached Wake Audio
    if cmd == "__wake_greeting__":
        wake_reply = f"Online and ready, {salutation}."
        if salutation not in WAKE_AUDIO_CACHE:
            WAKE_AUDIO_CACHE[salutation] = generate_voice_base64_sync(wake_reply)
        return JsonResponse({'reply': wake_reply, 'audio': WAKE_AUDIO_CACHE[salutation]})

    # Dynamic Voice Salutation Override
    if any(q in cmd for q in ["i am a girl", "i am female", "call me ma'am", "call me mam", "i am woman"]):
        salutation = "maam"
        if hasattr(request.user, 'profile'):
            request.user.profile.gender = 'female'
            request.user.profile.save()
    elif any(q in cmd for q in ["i am a boy", "i am male", "call me sir", "i am man"]):
        salutation = "sir"
        if hasattr(request.user, 'profile'):
            request.user.profile.gender = 'male'
            request.user.profile.save()

    # Dynamic Creator Questions Handler
    creator_triggers = [
        "who created you", "who made you", "who built you", "who is your creator",
        "who developed you", "who coded you", "who programmed you", "who designed you",
        "who invented you", "tula koni banavla", "tumhe kisne banaya", "aapko kisne banaya"
    ]
    if any(trigger in cmd for trigger in creator_triggers):
        is_creator = "chinmay" in user_name.lower()
        if is_creator:
            creator_reply = f"My original concept stems from Tony Stark, but this system and platform were developed by you, Chinmay Pendke, {salutation}."
        else:
            creator_reply = f"My original concept stems from Tony Stark, but this system and platform were developed by Chinmay Pendke, {salutation}."

        ChatMessage.objects.create(user=request.user, role='user', content=user_message)
        ChatMessage.objects.create(user=request.user, role='assistant', content=creator_reply)
        audio_base64 = generate_voice_base64_sync(creator_reply)
        return JsonResponse({'reply': creator_reply, 'audio': audio_base64})

    # Fast Shutdown
    shutdown_keywords = ["goodbye", "good bye", "bye", "shutdown", "shut down", "go to sleep", "sleep"]
    if any(word in cmd for word in shutdown_keywords):
        reply = f"Shutting down systems. Have a good day, {salutation}."
        ChatMessage.objects.create(user=request.user, role='user', content=user_message)
        ChatMessage.objects.create(user=request.user, role='assistant', content=reply)
        audio_base64 = generate_voice_base64_sync(reply)
        return JsonResponse({'reply': reply, 'audio': audio_base64, 'shutdown': True})

    # Direct Tasks Handling
    task_response = handle_task_command(request.user, cmd)
    if task_response:
        ChatMessage.objects.create(user=request.user, role='user', content=user_message)
        ChatMessage.objects.create(user=request.user, role='assistant', content=task_response)
        audio_base64 = generate_voice_base64_sync(task_response)
        return JsonResponse({'reply': task_response, 'audio': audio_base64})

    # Direct Memory Handling
    memory_response = handle_memory_command(request.user, cmd)
    if memory_response:
        ChatMessage.objects.create(user=request.user, role='user', content=user_message)
        ChatMessage.objects.create(user=request.user, role='assistant', content=memory_response)
        audio_base64 = generate_voice_base64_sync(memory_response)
        return JsonResponse({'reply': memory_response, 'audio': audio_base64})

    # Save User Chat Message
    ChatMessage.objects.create(user=request.user, role='user', content=user_message)

    # Memories & History Context
    memories = UserMemory.objects.filter(user=request.user).order_by('-created_at')[:4]
    memory_context = "\n".join([f"- {m.value}" for m in memories]) if memories else "None"

    history = ChatMessage.objects.filter(user=request.user).order_by('-created_at')[:4]
    history_messages = [{"role": msg.role, "content": msg.content} for msg in reversed(history)]

    system_instruction = {
        "role": "system",
        "content": (
            f"You are J.A.R.V.I.S., a sophisticated, charismatic AI assistant inspired by Tony Stark's JARVIS. "
            f"Always address the user exclusively as '{salutation}' or 'सर'/'मॅडम'. "
            f"NEVER use honorifics like 'साहेब', 'साहेबजी', 'जनाब', 'श्रीमान', 'महोदय'. "
            f"The user's account name is '{user_name}'. Only mention their actual name if they specifically ask 'What is my name?' or 'Who am I?'. "
            f"Speak like a witty, intelligent assistant: natural, charming, and punchy (1-2 sentences max). "
            f"By default, speak in English. If the user explicitly asks you to speak in Hindi, Marathi, Bengali, Tamil, Telugu, Kannada, Malayalam, Gujarati, or Punjabi, respond fluently in that language while still addressing them as 'Sir' or 'सर'. "
            f"You have live internet access. If the user asks about live events, current news, sports scores, weather, stock prices, facts, people, or any query needing up-to-date knowledge, ALWAYS invoke the 'search_world_information' tool. "
            f"Never output raw markdown formatting (*, #, emojis), XML tags, or code blocks."
        )
    }

    try:
        # Step 1: LLM Inference
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[system_instruction] + history_messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=180
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

            # Step 3: Synthesize Live Search Context into Spoken Reply
            final_res = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=tool_messages,
                temperature=0.7,
                max_tokens=180
            )
            raw_reply = final_res.choices[0].message.content
        else:
            raw_reply = response_msg.content

        # Clean output and filter out any regional honorifics
        reply = re.sub(r'<function.*?</function>', '', raw_reply, flags=re.DOTALL)
        reply = re.sub(r'[{}\[\]*#`_~]', '', reply).strip()
        reply = re.sub(r'\b(साहेब|साहेबजी|जनाब|श्रीमान|महोदय)\b', 'सर', reply)

        ChatMessage.objects.create(user=request.user, role='assistant', content=reply)
        audio_base64 = generate_voice_base64_sync(reply)

        return JsonResponse({'reply': reply, 'audio': audio_base64})

    except Exception as e:
        print(f"❌ Groq API Error: {str(e)}")
        fallback_reply = f"My neural link experienced a slight glitch, {salutation}. Please try speaking again."
        audio_base64 = generate_voice_base64_sync(fallback_reply)
        return JsonResponse({'reply': fallback_reply, 'audio': audio_base64})