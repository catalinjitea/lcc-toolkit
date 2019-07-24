# -*- coding: utf-8 -*-
# Generated by Django 1.11.22 on 2019-07-24 12:56
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('lcc', '0018_add_legispro_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='approve_url',
            field=models.URLField(blank=True, max_length=255, null=True, verbose_name='Approve URL'),
        ),
    ]
