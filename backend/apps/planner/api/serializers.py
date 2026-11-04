from rest_framework import serializers

MAX_LOAD_LB = 120_000  # beyond any permitted load; catches unit mistakes (kg vs lb)


class PlaceSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)
    label = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class PlanRequestSerializer(serializers.Serializer):
    origin = PlaceSerializer()
    destination = PlaceSerializer()
    departure_local = serializers.DateTimeField(
        required=False,
        allow_null=True,
        default=None,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        default_timezone=None,
        help_text="Wall-clock time at the origin, without a UTC offset. Omit for 'now'.",
    )
    load_lb = serializers.FloatField(min_value=1, max_value=MAX_LOAD_LB)
    interval_mi = serializers.ChoiceField(choices=[10, 25, 50], default=25)
    hos_enabled = serializers.BooleanField(default=True)

    def validate(self, attrs):
        o, d = attrs["origin"], attrs["destination"]
        if abs(o["lat"] - d["lat"]) < 1e-4 and abs(o["lng"] - d["lng"]) < 1e-4:
            raise serializers.ValidationError({"destination": "Origin and destination are the same place."})
        dep = attrs.get("departure_local")
        if dep is not None and dep.tzinfo is not None:
            attrs["departure_local"] = dep.replace(tzinfo=None)
        return attrs
