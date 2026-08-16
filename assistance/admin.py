#jarivs_web/assistance/admin.py
from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from .models import UserProfile, UserMemory, TaskItem, ChatMessage

# Unregister default User registration
admin.site.unregister(User)


# 1. Authentication and Authorization -> Users (ONLY Admins & Superusers)
@admin.register(User)
class SuperuserOnlyAdmin(UserAdmin):
    list_display = ('id', 'username', 'email', 'is_superuser', 'is_staff', 'last_login', 'date_joined')
    list_filter = ('is_superuser', 'date_joined')
    search_fields = ('username', 'email')
    ordering = ('-date_joined',)

    def get_queryset(self, request):
        # Strictly isolates staff and superuser accounts (e.g. jarvis)
        qs = super().get_queryset(request)
        return qs.filter(is_staff=True)


# 2. Assistance -> User profiles (ONLY Normal Jarvis App Users)
@admin.register(UserProfile)
class JarvisUserProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'city', 'get_date_joined')
    list_filter = ('city',)
    search_fields = ('user__username', 'city')
    ordering = ('-id',)
    actions = ['delete_selected_users_and_profiles']

    def get_queryset(self, request):
        # Strictly isolates standard voice assistant users
        qs = super().get_queryset(request)
        return qs.filter(user__is_staff=False, user__is_superuser=False)

    def get_date_joined(self, obj):
        return obj.user.date_joined
    get_date_joined.short_description = 'Date Joined'

    def delete_model(self, request, obj):
        # When deleted individually, delete the underlying User account
        user = obj.user
        super().delete_model(request, obj)
        if user:
            user.delete()

    def delete_queryset(self, request, queryset):
        # When deleted in bulk, delete all underlying User accounts
        user_ids = list(queryset.values_list('user_id', flat=True))
        queryset.delete()
        User.objects.filter(id__in=user_ids).delete()


# 3. Directives / Tasks
@admin.register(TaskItem)
class TaskItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'title', 'is_completed', 'created_at')
    list_filter = ('is_completed', 'created_at', 'user')
    search_fields = ('user__username', 'title')
    list_editable = ('is_completed',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(user__is_staff=False)


# 4. Permanent Memory Logs
@admin.register(UserMemory)
class UserMemoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'key', 'value', 'created_at')
    list_filter = ('user', 'created_at')
    search_fields = ('user__username', 'key', 'value')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(user__is_staff=False)


# 5. Conversation Logs
@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'role', 'short_content', 'created_at')
    list_filter = ('role', 'created_at', 'user')
    search_fields = ('user__username', 'content')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)

    def short_content(self, obj):
        return obj.content[:65] + "..." if len(obj.content) > 65 else obj.content
    short_content.short_description = "Message Preview"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(user__is_staff=False)