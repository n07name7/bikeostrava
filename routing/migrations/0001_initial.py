# Generated migration — no PostGIS dependency

from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='AccidentPoint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('latitude', models.FloatField(db_index=True)),
                ('longitude', models.FloatField(db_index=True)),
                ('date', models.DateField(blank=True, null=True)),
                ('severity', models.CharField(blank=True, max_length=50)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'indexes': [
                    models.Index(fields=['date'], name='routing_acc_date_idx'),
                    models.Index(fields=['latitude', 'longitude'], name='routing_acc_latlng_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='RouteCache',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_normalized', models.CharField(db_index=True, max_length=500)),
                ('end_normalized', models.CharField(db_index=True, max_length=500)),
                ('result_json', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'unique_together': {('start_normalized', 'end_normalized')},
                'indexes': [
                    models.Index(fields=['created_at'], name='routing_rc_created_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='SavedRoute',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('start_address', models.CharField(max_length=500)),
                ('end_address', models.CharField(max_length=500)),
                ('start_lat', models.FloatField()),
                ('start_lng', models.FloatField()),
                ('end_lat', models.FloatField()),
                ('end_lng', models.FloatField()),
                ('safety_score', models.IntegerField()),
                ('distance_km', models.FloatField(default=0)),
                ('duration_min', models.IntegerField(default=0)),
                ('route_data', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
