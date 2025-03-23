from django.contrib import admin
from .models import *

# Register your models here.
admin.site.register(User)
admin.site.register(Role)
admin.site.register(Course)
admin.site.register(Lesson)
admin.site.register(Quiz)
admin.site.register(Question)
admin.site.register(Submission)
admin.site.register(Grade)
