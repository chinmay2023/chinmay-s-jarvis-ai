import os
import base64
import asyncio
import edge_tts
from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from openai import OpenAI
from duckduckgo_search import DDGS
from .models import ChatMessage

# Initialize Groq client
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

# Deep American Male Neural AI Voice (No British Accent)
JARVIS_VOICE = "en-US-ChristopherNeural"


async def generate_voice_base64(text):
    """Generates deep, bass-rich male audio using Edge-TTS."""
    clean_text = text.replace('*', '').replace('#', '').strip()
    try:
        # pitch="-14Hz" provides deep bass resonance; rate="-2%" keeps it calm and authoritative
        communicate = edge_tts.Communicate(
            clean_text, 
            JARVIS_VOICE, 
            rate="-2%", 
            pitch="-14Hz"
        )
        audio_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
        return base64.b64encode(audio_data).decode('utf-8')
    except Exception as e:
        print(f"⚠️ Edge-TTS Error: {e}")
        return None


def detect_salutation(username):
    """Uses Groq Llama to dynamically detect if a name/username is male or female."""
    clean_name = username.lower().strip()

    female_terms = ["girl", "woman", "female", "lady", "miss", "mrs", "ms"]
    if any(term in clean_name for term in female_terms):
        return "maam"

    try:
        prompt = (
            f"Determine if the username/name '{username}' is typically Male or Female. "
            f"Respond with ONLY 'sir' for male or 'maam' for female. Do not include any punctuation."
        )

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=5
        )

        result = response.choices[0].message.content.strip().lower()
        if "maam" in result or "female" in result or "ma'am" in result:
            return "maam"
        return "sir"
    except Exception as e:
        print(f"⚠️ Salutation Detection Error: {e}")
        return "sir"


def get_live_web_context(query):
    """Fetches real-time web search context ONLY for time/fact-sensitive queries."""
    search_triggers = ["today", "news", "weather", "score", "price", "who is", "latest", "time", "date", "search"]
    
    if not any(trigger in query.lower() for trigger in search_triggers):
        return ""

    try:
        results = list(DDGS().text(query, max_results=2))
        if results:
            snippets = [r.get('body', '') for r in results if r.get('body')]
            return " ".join(snippets)
    except Exception as e:
        print(f"⚠️ Live search error: {e}")
    return ""


def register_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('assistance:login')
    else:
        form = UserCreationForm()
    return render(request, 'assistance/register.html', {'form': form})


@login_required(login_url='assistance:login')
def chat_view(request):
    salutation = detect_salutation(request.user.username)
    return render(request, 'assistance/chat.html', {'salutation': salutation})


@login_required(login_url='assistance:login')
def jarvis_api(request):
    if request.method == 'POST':
        user_message = request.POST.get('message', '')
        user_name = request.user.username
        
        if not user_message:
            return JsonResponse({'error': 'No audio input received'}, status=400)

        salutation = detect_salutation(user_name)
        cmd = user_message.lower().strip()

        # Handle Wake Greeting Request
        if cmd == "__wake_greeting__":
            wake_reply = f"Online and ready, {salutation}."
            audio_base64 = asyncio.run(generate_voice_base64(wake_reply))
            return JsonResponse({'reply': wake_reply, 'audio': audio_base64})

        # Dynamic salutation voice override
        if "i am a girl" in cmd or "i am female" in cmd or "call me ma'am" in cmd or "call me mam" in cmd:
            salutation = "maam"
        elif "i am a boy" in cmd or "i am male" in cmd or "call me sir" in cmd:
            salutation = "sir"

        # Fast Shutdown / Goodbye
        shutdown_keywords = ["goodbye", "good bye", "bye", "shutdown", "shut down", "go to sleep", "sleep"]
        if any(word in cmd for word in shutdown_keywords):
            reply = f"Shutting down systems. Have a good day, {salutation}."
            ChatMessage.objects.create(user=request.user, role='user', content=user_message)
            ChatMessage.objects.create(user=request.user, role='assistant', content=reply)
            
            audio_base64 = asyncio.run(generate_voice_base64(reply))
            return JsonResponse({'reply': reply, 'audio': audio_base64, 'shutdown': True})

        # Save User Message
        ChatMessage.objects.create(user=request.user, role='user', content=user_message)

        # Context Memory
        history = ChatMessage.objects.filter(user=request.user).order_by('-created_at')[:4]
        history_messages = [{"role": msg.role, "content": msg.content} for msg in reversed(history)]

        # Conditional Web Search
        live_context = get_live_web_context(user_message)
        live_info_prompt = f"\n[Live Context]: {live_context}" if live_context else ""

        # Creator check
        is_creator_logged_in = user_name.lower().startswith("chinmay")

        if is_creator_logged_in:
            creator_instruction = (
                f"ONLY IF explicitly asked 'who created you', 'who built you', or 'who developed you', state: "
                f"'My original concept stems from Tony Stark, but this system and platform were developed by you, Chinmay Pendke.' "
                f"For all other questions, answer directly without mentioning your creator or origin."
            )
        else:
            creator_instruction = (
                f"ONLY IF explicitly asked 'who created you', 'who built you', or 'who developed you', state: "
                f"'My original concept stems from Tony Stark, but this system and platform were developed by Chinmay Pendke.' "
                f"For all other questions, answer directly without mentioning your creator or origin."
            )

        other_salutation = "sir" if salutation == "maam" else "maam"

        system_instruction = {
            "role": "system",
            "content": (
                f"You are J.A.R.V.I.S., Tony Stark's highly intelligent AI assistant. "
                f"Address the user strictly as '{salutation}'. NEVER address them as '{other_salutation}'. "
                f"ALWAYS speak strictly in clear English. "
                f"If the user asks 'Who am I?', 'What is my name?', or 'Do you know my name?', answer directly using their name: 'Your name is {user_name}, {salutation}.' Do not use the word 'username'. "
                f"{creator_instruction} "
                f"{live_info_prompt} "
                f"Be conversational, natural, and punchy (1 to 2 sentences max). "
                f"Never use markdown formatting like asterisks (*), emojis, or code blocks."
            )
        }

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[system_instruction] + history_messages,
                temperature=0.5,
                max_tokens=200
            )
            reply = response.choices[0].message.content

            ChatMessage.objects.create(user=request.user, role='assistant', content=reply)

            # Generate deep American male neural audio
            audio_base64 = asyncio.run(generate_voice_base64(reply))

            return JsonResponse({'reply': reply, 'audio': audio_base64})
        except Exception as e:
            print(f"❌ Groq API Error: {str(e)}")
            fallback_reply = f"My neural link experienced a slight glitch, {salutation}. Please try speaking again."
            audio_base64 = asyncio.run(generate_voice_base64(fallback_reply))
            return JsonResponse({'reply': fallback_reply, 'audio': audio_base64})

    return JsonResponse({'error': 'Invalid request method'}, status=405)