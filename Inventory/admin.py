from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Equipment,Department

admin.site.register(Equipment),
admin.site.register(Department),
