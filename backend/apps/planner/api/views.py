from django.core.cache import cache
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from ..providers.base import Place
from ..providers.registry import get_providers
from ..services.heatmap import build_heatmap
from ..services.planner import PlanRequest, build_plan
from .serializers import PlanRequestSerializer


@api_view(["GET"])
@throttle_classes([])
def health(request):
    return Response({"status": "ok", "mode": get_providers().mode})


class GeocodeView(APIView):
    throttle_scope = "geocode"

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response({"results": []})
        key = f"geo:{get_providers().mode}:{q.lower()[:100]}"
        results = cache.get(key)
        if results is None:
            results = [vars(p) for p in get_providers().geocoder.search(q[:100], limit=6)]
            cache.set(key, results, timeout=86400)
        return Response({"results": results})


class PlanView(APIView):
    throttle_scope = "plan"

    def post(self, request):
        s = PlanRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        req = PlanRequest(
            origin=Place(**d["origin"]),
            destination=Place(**d["destination"]),
            departure_local=d["departure_local"],
            load_lb=d["load_lb"],
            interval_mi=int(d["interval_mi"]),
            hos_enabled=d["hos_enabled"],
        )
        return Response(build_plan(req))


class HeatmapView(APIView):
    throttle_scope = "heatmap"

    def get(self, request, plan_id: str):
        key = f"heatmap:{plan_id}"
        data = cache.get(key)
        if data is None:
            plan = cache.get(f"plan:{plan_id}")
            if plan is None:
                raise NotFound("This plan has expired. Please plan the trip again.")
            data = build_heatmap(plan)
            cache.set(key, data, timeout=1800)
        return Response(data)
