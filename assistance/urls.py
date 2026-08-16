from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'assistance'

urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('api/jarvis/', views.jarvis_api, name='jarvis_api'),
    path('api/clear-history/', views.clear_history_api, name='clear_history'),
    path('api/tasks/', views.get_tasks_api, name='get_tasks'),
    path('register/', views.register_view, name='register'),
    path('login/', auth_views.LoginView.as_view(template_name='assistance/login.html'), name='login'),
    path('logout/', views.logout_view, name='logout'),
]