# jarvis_web/assistance/urls.py
from django.urls import path
from . import views

app_name = 'assistance'

urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('api/jarvis/', views.jarvis_api, name='jarvis_api'),
    path('api/clear-history/', views.clear_history_api, name='clear_history'),
    path('api/tasks/', views.get_tasks_api, name='get_tasks'),
]