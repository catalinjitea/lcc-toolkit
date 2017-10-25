# -*- coding: utf-8 -*-
# Generated by Django 1.11.5 on 2017-10-23 05:52
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('lcc', '0004_auto_20171018_1411'),
    ]

    operations = [
        migrations.AlterField(
            model_name='legislation',
            name='abstract',
            field=models.CharField(blank=True, max_length=1024, null=True),
        ),
        migrations.AlterField(
            model_name='legislation',
            name='law_type',
            field=models.CharField(choices=[('Law', 'Law'), ('Constitution', 'Constitution'), ('Regulation', 'Regulation'), ('oth', 'Other')], default=('Law', 'Law'), max_length=64),
        ),
    ]
