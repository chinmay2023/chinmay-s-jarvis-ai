from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from .models import UserProfile, UserMemory, TaskItem, ChatMessage


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile'


# Re-register UserAdmin with inline profile
admin.site.unregister(User)

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('id', 'username', 'email', 'get_gender', 'get_city', 'date_joined', 'is_staff')
    list_filter = ('is_staff', 'is_superuser', 'date_joined')
    search_fields = ('username', 'email')
    ordering = ('-date_joined',)

    def get_gender(self, obj):
        return obj.profile.gender if hasattr(obj, 'profile') else '-'
    get_gender.short_description = 'Gender'

    def get_city(self, obj):
        return obj.profile.city if hasattr(obj, 'profile') else '-'
    get_city.short_description = 'City'


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'gender', 'city')
    list_filter = ('gender', 'city')
    search_fields = ('user__username', 'city')


@admin.register(UserMemory)
class UserMemoryAdmin(admin.ModelAdmin):
    list_display = ('user', 'key', 'value', 'created_at')
    list_filter = ('user', 'created_at')
    search_fields = ('user__username', 'key', 'value')


@admin.register(TaskItem)
class TaskItemAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'is_completed', 'created_at')
    list_filter = ('is_completed', 'created_at', 'user')
    search_fields = ('user__username', 'title')
    list_editable = ('is_completed',)


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