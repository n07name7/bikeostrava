import uuid
import django.contrib.gis.db.models.fields
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='AccidentPoint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('location', django.contrib.gis.db.models.fields.PointField(srid=4326)),
                ('date', models.DateField(blank=True, null=True)),
                ('severity', models.CharField(blank=True, max_length=50)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
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
        migrations.AddIndex(
            model_name='accidentpoint',
            index=models.Index(fields=['date'], name='routing_acc_date_idx'),
        ),
        migrations.AddIndex(
            model_name='routecache',
            index=models.Index(fields=['created_at'], name='routing_rou_created_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='routecache',
            unique_together={('start_normalized', 'end_normalized')},
        ),
    ]
