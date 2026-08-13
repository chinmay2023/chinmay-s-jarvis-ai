import os
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


def get_live_web_context(query):
    """Fetches real-time web search context for up-to-date knowledge."""
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
    return render(request, 'assistance/chat.html')


@login_required(login_url='assistance:login')
def jarvis_api(request):
    if request.method == 'POST':
        user_message = request.POST.get('message', '')
        user_name = request.user.username
        
        if not user_message:
            return JsonResponse({'error': 'No audio input received'}, status=400)

        cmd = user_message.lower().strip()

        # Shutdown / Goodbye Detection
        shutdown_keywords = ["goodbye", "good bye", "bye", "shutdown", "shut down", "go to sleep", "sleep"]
        if any(word in cmd for word in shutdown_keywords):
            reply = "Shutting down systems. Have a good day, sir."
            ChatMessage.objects.create(user=request.user, role='user', content=user_message)
            ChatMessage.objects.create(user=request.user, role='assistant', content=reply)
            return JsonResponse({'reply': reply, 'shutdown': True})

        # Save User Message to Database
        ChatMessage.objects.create(user=request.user, role='user', content=user_message)

        # Retrieve Conversation Context Memory (Last 8 Exchanges)
        history = ChatMessage.objects.filter(user=request.user).order_by('-created_at')[:8]
        history_messages = [{"role": msg.role, "content": msg.content} for msg in reversed(history)]

        # Fetch Live Search Context for Up-To-Date Knowledge
        live_context = get_live_web_context(user_message)
        live_info_prompt = f"\n[Live Web Context for current facts]: {live_context}" if live_context else ""

        # Check if the logged-in user is developer Chinmay (case-insensitive check)
        is_creator_logged_in = user_name.lower().startswith("chinmay")

        # Creator instruction triggered ONLY when explicitly asked about identity/creator
        if is_creator_logged_in:
            creator_instruction = (
                f"ONLY IF explicitly asked 'who created you', 'who built you', or 'who developed you', state: "
                f"'My original concept stems from Tony Stark, but this system and platform were developed by you, Chinmay Pendke.' "
                f"For all other questions (technical, general advice, etc.), answer directly without mentioning your creator or origin."
            )
        else:
            creator_instruction = (
                f"ONLY IF explicitly asked 'who created you', 'who built you', or 'who developed you', state: "
                f"'My original concept stems from Tony Stark, but this system and platform were developed by Chinmay Pendke.' "
                f"For all other questions (technical, general advice, etc.), answer directly without mentioning your creator or origin."
            )

        # Dynamic System Prompt locked strictly to English
        system_instruction = {
            "role": "system",
            "content": (
                f"You are J.A.R.V.I.S., Tony Stark's highly intelligent AI assistant. "
                f"Address the user respectfully as 'sir'. "
                f"ALWAYS speak and respond strictly in clear English. "
                f"Only mention the logged-in username ({user_name}) if explicitly asked 'Who am I?' or 'What is my username?'. "
                f"{creator_instruction} "
                f"{live_info_prompt} "
                f"Keep all answers natural, spoken-friendly, concise (1 to 2 sentences max). "
                f"Never use markdown formatting like asterisks (*), emojis, or code blocks."
            )
        }

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[system_instruction] + history_messages,
                temperature=0.4
            )
            reply = response.choices[0].message.content

            # Save Assistant Reply to Database
            ChatMessage.objects.create(user=request.user, role='assistant', content=reply)

            return JsonResponse({'reply': reply})
        except Exception as e:
            print(f"❌ Groq API Error: {str(e)}")
            return JsonResponse({'reply': 'My neural link experienced a slight glitch, sir. Please try speaking again.'})

    return JsonResponse({'error': 'Invalid request method'}, status=405)