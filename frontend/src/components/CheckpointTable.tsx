import { fmtLocal, level } from "../lib/format";
import { useUi } from "../store";
import type { Route } from "../types";

export function CheckpointTable({ route }: { route: Route }) {
  const hover = useUi((s) => s.hoverCheckpoint);
  return (
    <details className="checkpoints">
      <summary>
        Checkpoints on Route {route.id} ({route.checkpoints.length})
      </summary>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Mile</th>
              <th scope="col">Arrive</th>
              <th scope="col">Risk</th>
              <th scope="col" className="num">
                Wind
              </th>
              <th scope="col" className="num">
                Rain
              </th>
              <th scope="col" className="num">
                Snow
              </th>
            </tr>
          </thead>
          <tbody>
            {route.checkpoints.map((cp) => {
              const lv = level(cp.level);
              return (
                <tr
                  key={cp.i}
                  tabIndex={0}
                  onMouseEnter={() => hover({ route: route.id, i: cp.i })}
                  onFocus={() => hover({ route: route.id, i: cp.i })}
                  onMouseLeave={() => hover(null)}
                >
                  <td className="num">{Math.round(cp.mile)}</td>
                  <td>{fmtLocal(cp.eta_utc, cp.tz)}</td>
                  <td>
                    <span className="lv-pill" style={{ ["--c" as string]: lv.color }}>
                      {lv.label}
                    </span>
                    {cp.load_escalated && <span className="muted small"> load</span>}
                  </td>
                  <td className="num">{cp.wx.wind_mph != null ? Math.round(cp.wx.wind_mph) : "—"}</td>
                  <td className="num">{cp.wx.rain_in != null ? cp.wx.rain_in.toFixed(2) : "—"}</td>
                  <td className="num">{cp.wx.snow_in != null ? cp.wx.snow_in.toFixed(1) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="muted small">Wind in mph; rain and snow in inches per hour, for the hour the truck passes.</p>
      </div>
    </details>
  );
}
