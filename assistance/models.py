#jarvis_web/assistance/models.py
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserProfile(models.Model):
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, default='male')
    city = models.CharField(max_length=100, default='Wardha', help_text="Default city for weather/news")

    def __str__(self):
        return f"{self.user.username} - {self.gender} ({self.city})"


@receiver(post_save, sender=User)
def create_or_save_user_profile(sender, instance, created, **kwargs):
    """Automatically creates or updates UserProfile whenever a User is created."""
    if created:
        UserProfile.objects.create(user=instance)
    else:
        if hasattr(instance, 'profile'):
            instance.profile.save()


class UserMemory(models.Model):
    """Stores long-term facts and user preferences that JARVIS remembers."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='memories')
    key = models.CharField(max_length=100, help_text="Fact key, e.g., 'favorite_player', 'profession'")
    value = models.TextField(help_text="Fact value, e.g., 'Virat Kohli', 'Software Engineer'")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "User Memories"

    def __str__(self):
        return f"{self.user.username} | {self.key}: {self.value[:30]}"


class TaskItem(models.Model):
    """Stores voice-created todo list items."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=255)
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        status = "✓" if self.is_completed else "✗"
        return f"[{status}] {self.user.username}: {self.title}"


class ChatMessage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_messages')
    role = models.CharField(max_length=20)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.role}): {self.content[:30]}"