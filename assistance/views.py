# jarvis_web/assistance/views.py
import os
import re
import json
import base64
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Auto-load .env from root and parent directories
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv()

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

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None

from .forms import CustomUserCreationForm, detect_gender_from_name
from .models import ChatMessage, UserProfile, UserMemory, TaskItem

# Initialize Wikipedia API client
wiki = wikipediaapi.Wikipedia(
    language='en',
    user_agent='JarvisAI/2.0 (voice_assistant_project; contact@jarvisai.local)'
)

# Initialize Tavily AI client
tavily_api_key = os.getenv("TAVILY_API_KEY")
tavily_client = TavilyClient(api_key=tavily_api_key) if (TavilyClient and tavily_api_key) else None

# Active Supported Production Models on Groq
ACTIVE_GROQ_MODEL = "openai/gpt-oss-120b"
FAST_FALLBACK_MODEL = "openai/gpt-oss-20b"

# Default Signature JARVIS Voice
DEFAULT_VOICE = "en-US-ChristopherNeural"
DEFAULT_PITCH = "-10Hz"
DEFAULT_RATE = "-6%"

# Neural Voice Model Mapping for Indian Scripts
INDIAN_VOICE_MAP = [
    (r'[\u0980-\u09FF]', 'bn-IN-BashkarNeural', '+0Hz', '+0%'),
    (r'[\u0B80-\u0BFF]', 'ta-IN-ValluvarNeural', '+0Hz', '+0%'),
    (r'[\u0C00-\u0C7F]', 'te-IN-MohanNeural', '+0Hz', '+0%'),
    (r'[\u0C80-\u0CFF]', 'kn-IN-GaganNeural', '+0Hz', '+0%'),
    (r'[\u0D00-\u0D7F]', 'ml-IN-MidhunNeural', '+0Hz', '+0%'),
    (r'[\u0A80-\u0AFF]', 'gu-IN-NiranjanNeural', '+0Hz', '+0%'),
    (r'[\u0A00-\u0A7F]', 'pa-IN-OjasNeural', '+0Hz', '+0%'),
    (r'[\u0600-\u06FF]', 'ur-IN-SalmanNeural', '+0Hz', '+0%'),
    (r'[\u0900-\u097F]', 'mr-IN-ManoharNeural', '+0Hz', '+0%'),
]

WAKE_AUDIO_CACHE = {}
AUDIO_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def get_voice_parameters(text):
    """Detects native script or phonetic cues and assigns the appropriate tuned neural voice."""
    for pattern, voice, pitch, rate in INDIAN_VOICE_MAP:
        if re.search(pattern, text):
            return voice, pitch, rate

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
    communicate = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
    return b"".join(audio_chunks) if audio_chunks else None


def generate_voice_base64_sync(text):
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
    if hasattr(user, 'profile') and user.profile.gender:
        return "maam" if user.profile.gender == 'female' else "sir"

    try:
        detected = detect_gender_from_name(user.username)
        return "maam" if detected == "female" else "sir"
    except Exception:
        return "sir"


def execute_web_search(query, default_city="Wardha"):
    clean_q = query.lower().strip()

    # 1. Weather
    if any(w in clean_q for w in ["weather", "temperature", "mausam", "havaman", "forecast", "climate"]):
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

    # 2. Tavily AI Real-Time Search
    if tavily_client:
        try:
            search_context = tavily_client.get_search_context(
                query=query,
                search_depth="basic",
                max_results=3
            )
            if search_context:
                return str(search_context)
        except Exception as e:
            print(f"⚠️ Tavily Search Notice: {e}")

    # 3. DuckDuckGo Search Fallback
    if DDGS:
        try:
            results = list(DDGS().text(query, max_results=3))
            if results:
                snippets = [f"- {r.get('title', '')}: {r.get('body', '')}" for r in results if r.get('body')]
                return "\n".join(snippets)
        except Exception as e:
            print(f"⚠️ DuckDuckGo Notice: {e}")

    # 4. Wikipedia Search Fallback
    try:
        clean_topic = re.sub(r'^(who is|what is|tell me about|explain|define|where is|founder of)\s+', '', clean_q).strip('?')
        if len(clean_topic) > 2:
            page = wiki.page(clean_topic)
            if page.exists():
                sentences = page.summary.split('. ')[:2]
                return ". ".join(sentences) + "."
    except Exception as e:
        print(f"⚠️ Wikipedia Notice: {e}")

    return "No verified live information found on this topic."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "manage_tasks",
            "description": "Adds, lists, or clears user directives/tasks in the database. When action is 'add', ALWAYS extract a concise, clean action title (2 to 6 words max) without speech corrections or filler words.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "clear"],
                        "description": "The action to perform on user directives."
                    },
                    "task_title": {
                        "type": "string",
                        "description": "Clean, summarized directive title (e.g., 'Meditate in 5 minutes', 'Buy groceries', 'Call Tony Stark')."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_world_information",
            "description": "Searches the live internet for current events, news, songs, music, live sports, real-time weather, stock prices, definitions, facts, people, or any world knowledge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The exact search query to look up."
                    }
                },
                "required": ["query"]
            }
        }
    }
]


@ensure_csrf_cookie
def login_view(request):
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
    auth_logout(request)
    response = redirect('assistance:login')
    response.delete_cookie('sessionid', path='/')
    return response


@ensure_csrf_cookie
def chat_view(request):
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
    tasks = TaskItem.objects.filter(user=request.user, is_completed=False).order_by('-created_at')
    task_data = [{'id': t.id, 'title': t.title, 'is_completed': t.is_completed} for t in tasks]
    return JsonResponse({'tasks': task_data})


@login_required(login_url='assistance:login')
def jarvis_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    if not User.objects.filter(id=request.user.id).exists():
        return JsonResponse({'error': 'Session expired. Please log in again.'}, status=401)

    user_message = request.POST.get('message', '').strip()
    user_name = request.user.username
    salutation = get_user_salutation(request.user)

    if not user_message or len(user_message) < 2:
        reply = f"I am listening, {salutation}. Please speak clearly."
        audio_base64 = generate_voice_base64_sync(reply)
        return JsonResponse({'reply': reply, 'audio': audio_base64})

    cmd = user_message.lower()

    # Pre-Cached Wake Audio
    if cmd == "__wake_greeting__":
        wake_reply = f"Online and ready, {salutation}."
        if salutation not in WAKE_AUDIO_CACHE:
            WAKE_AUDIO_CACHE[salutation] = generate_voice_base64_sync(wake_reply)
        return JsonResponse({'reply': wake_reply, 'audio': WAKE_AUDIO_CACHE[salutation]})

    # Fast Shutdown
    shutdown_keywords = ["goodbye", "good bye", "bye", "shutdown", "shut down", "go to sleep", "sleep"]
    if any(word in cmd for word in shutdown_keywords):
        reply = f"Shutting down systems. Have a good day, {salutation}."
        ChatMessage.objects.create(user=request.user, role='user', content=user_message)
        ChatMessage.objects.create(user=request.user, role='assistant', content=reply)
        audio_base64 = generate_voice_base64_sync(reply)
        return JsonResponse({'reply': reply, 'audio': audio_base64, 'shutdown': True})

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

    ChatMessage.objects.create(user=request.user, role='user', content=user_message)

    # Memories & History
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
            f"Known facts about this user:\n{memory_context}\n"
            f"Speak like a witty, intelligent assistant: natural, articulate, and punchy (1-2 sentences max). "
            f"By default, speak in English. If the user speaks or asks in Hindi, Marathi, Bengali, Tamil, Telugu, Kannada, Malayalam, Gujarati, or Punjabi, respond fluently in that language while still addressing them as '{salutation}' or 'सर'/'मॅडम'. "
            f"If the user asks to add, create, schedule, note, or remind them of a task/directive, ALWAYS call the 'manage_tasks' tool with a concise, clean action title (2 to 6 words max). "
            f"If the user asks about live events, songs, lyrics, current news, sports, weather, stock prices, or general facts, ALWAYS invoke the 'search_world_information' tool. "
            f"Never output raw markdown formatting (*, #, emojis), XML tags, or code blocks."
        )
    }

    raw_key = os.getenv("GROQ_API_KEY", "")
    groq_api_key = raw_key.strip().strip("'").strip('"')

    try:
        llm_client = OpenAI(
            api_key=groq_api_key,
            base_url="https://api.groq.com/openai/v1"
        )

        messages_payload = [system_instruction] + history_messages
        response = llm_client.chat.completions.create(
            model=ACTIVE_GROQ_MODEL,
            messages=messages_payload,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.4,
            max_tokens=180
        )
        response_msg = response.choices[0].message

        # Handle tool execution
        if response_msg.tool_calls:
            tool_messages = [
                system_instruction,
                {"role": "user", "content": user_message},
                response_msg
            ]

            default_city = request.user.profile.city if hasattr(request.user, 'profile') else "Wardha"

            for tool_call in response_msg.tool_calls:
                func_name = tool_call.function.name
                try:
                    args = json.loads(tool_call.function.arguments)
                except Exception:
                    args = {}

                if func_name == "manage_tasks":
                    action = args.get("action", "list")
                    if action == "add":
                        raw_title = args.get("task_title", "New Directive")
                        clean_title = re.sub(r'^(to|that|i have to|please)\s+', '', raw_title, flags=re.IGNORECASE).strip().capitalize()
                        TaskItem.objects.create(user=request.user, title=clean_title)
                        tool_content = f"Successfully added directive: '{clean_title}'."
                    elif action == "clear":
                        TaskItem.objects.filter(user=request.user).update(is_completed=True)
                        tool_content = "All directives have been cleared."
                    else:
                        active_tasks = TaskItem.objects.filter(user=request.user, is_completed=False).order_by('-created_at')[:5]
                        tool_content = ", ".join([t.title for t in active_tasks]) if active_tasks else "No active directives."

                    tool_messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": "manage_tasks",
                        "content": tool_content
                    })

                elif func_name == "search_world_information":
                    search_query = args.get("query", user_message)
                    search_results = execute_web_search(search_query, default_city=default_city)

                    tool_messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": "search_world_information",
                        "content": str(search_results)
                    })

            # Pass tools and tool_choice in synthesis call to prevent 400 error
            final_res = llm_client.chat.completions.create(
                model=ACTIVE_GROQ_MODEL,
                messages=tool_messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.4,
                max_tokens=180
            )
            raw_reply = final_res.choices[0].message.content or ""
        else:
            raw_reply = response_msg.content or ""

    except Exception as llm_err:
        print("❌ [PRIMARY FAILED, ATTEMPTING FAST FALLBACK MODEL]:", llm_err)

        try:
            fallback_res = llm_client.chat.completions.create(
                model=FAST_FALLBACK_MODEL,
                messages=[system_instruction, {"role": "user", "content": user_message}],
                temperature=0.5,
                max_tokens=180
            )
            raw_reply = fallback_res.choices[0].message.content or ""
        except Exception as final_err:
            print("❌ [FALLBACK FAILED]:", final_err)
            raw_reply = f"All systems are online, {salutation}."

    reply = re.sub(r'<function.*?</function>', '', raw_reply, flags=re.DOTALL)
    reply = re.sub(r'[{}\[\]*#`_~]', '', reply).strip()
    reply = re.sub(r'\b(साहेब|साहेबजी|जनाब|श्रीमान|महोदय)\b', 'सर', reply)

    if not reply:
        reply = f"All systems operational, {salutation}."

    ChatMessage.objects.create(user=request.user, role='assistant', content=reply)
    audio_base64 = generate_voice_base64_sync(reply)

    return JsonResponse({'reply': reply, 'audio': audio_base64})