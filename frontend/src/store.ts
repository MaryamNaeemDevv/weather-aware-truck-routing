import { create } from "zustand";
import type { HeatLayer } from "./types";

interface UiState {
  selectedRoute: string | null;
  hoveredCheckpoint: { route: string; i: number } | null;
  hour: number; // slider position, hours from the heatmap start (now)
  layer: HeatLayer;
  showHeatmap: boolean;
  select: (id: string | null) => void;
  hoverCheckpoint: (cp: { route: string; i: number } | null) => void;
  setHour: (h: number) => void;
  setLayer: (l: HeatLayer) => void;
  toggleHeatmap: () => void;
}

export const useUi = create<UiState>((set) => ({
  selectedRoute: null,
  hoveredCheckpoint: null,
  hour: 0,
  layer: "overall",
  showHeatmap: true,
  select: (id) => set({ selectedRoute: id }),
  hoverCheckpoint: (cp) => set({ hoveredCheckpoint: cp }),
  setHour: (h) => set({ hour: Math.max(0, Math.min(48, h)) }),
  setLayer: (l) => set({ layer: l }),
  toggleHeatmap: () => set((s) => ({ showHeatmap: !s.showHeatmap })),
}));
