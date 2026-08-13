from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'assistance'

urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('api/jarvis/', views.jarvis_api, name='jarvis_api'),
    path('register/', views.register_view, name='register'),
    path('login/', auth_views.LoginView.as_view(template_name='assistance/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]