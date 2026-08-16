# jarvis_web/assistance/forms.py
import re
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import UserProfile


def detect_gender_from_name(name_str: str) -> str:
    """Dynamically detects gender from a username or name."""
    if not name_str:
        return "male"

    first_name = re.split(r'[\s_\-\.\d]+', name_str.strip())[0].capitalize()
    if not first_name:
        return "male"

    try:
        import gender_guesser.detector as gender
        d = gender.Detector(case_sensitive=False)
        detected = d.get_gender(first_name)
        if detected in ["mostly_male", "male"]:
            return "male"
        elif detected in ["mostly_female", "female"]:
            return "female"
    except Exception:
        pass

    female_endings = ('a', 'i', 'ee', 'ya', 'ka', 'ti', 'ni', 'ta', 'ri', 'shree', 'shri', 'devi')
    if first_name.lower().endswith(female_endings):
        return "female"

    return "male"


class CustomUserCreationForm(UserCreationForm):

    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = False
        user.is_superuser = False

        if commit:
            user.save()
            detected_gender = detect_gender_from_name(user.username)
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.gender = detected_gender
            profile.save()

        return user