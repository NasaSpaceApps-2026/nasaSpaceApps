"""Steady-state vehicle speed on a grade, from the same force balance as the browser rover
(web/src/rover/physics.ts climbSpeed): drive limited by motor power and by the grip of the
driven wheels, against gravity and rolling resistance under Mars gravity."""
import math

MARS_G = 3.71


def has_force_model(prof: dict) -> bool:
    return all(k in prof for k in ("mass_kg", "power_w", "traction_mu", "rolling_coeff"))


def grade_speed(prof: dict, grade_deg: float, boost: bool = False) -> tuple[float, str | None]:
    """Mission speed (m/s) on a signed grade (+ uphill along travel). Returns (speed, stall_note);
    speed 0 with a note when the driven wheels cannot grip hard enough to climb."""
    th = math.radians(grade_deg)
    m = prof["mass_kg"]
    need = m * MARS_G * (math.sin(th) + prof["rolling_coeff"] * math.cos(th))
    geom = all(k in prof for k in ("wheelbase_m", "cg_height_m", "cg_forward_m"))
    if geom:
        # Rear-drive axle loads from moments about the contacts (steady climb, uphill transfer to the rear).
        L, hc = prof["wheelbase_m"], prof["cg_height_m"]
        a_r, a_f = L / 2 + prof["cg_forward_m"], L / 2 - prof["cg_forward_m"]
        grip = prof["traction_mu"] * m * MARS_G * (math.cos(th) * a_f + math.sin(th) * hc) / L
        # Wheelie control keeps 15 % of the weight on the front wheels (as the browser motor does).
        if math.cos(th) * a_r - math.sin(th) * hc < 0.15 * math.cos(th) * L:
            lim = math.degrees(math.atan((a_r - 0.15 * L) / hc))
            return 0.0, (f"front wheels would lift on a {grade_deg:.0f} deg climb; wheelie control limits "
                         f"climbs to ~{lim:.0f} deg")
    else:
        # Static share of the driven axle(s) as a conservative estimate.
        grip = prof["traction_mu"] * m * MARS_G * math.cos(th) * prof.get("driven_load_share", 1.0)
    if need >= grip:
        return 0.0, f"wheels cannot grip on a {grade_deg:.0f} deg climb (driven-wheel grip limit)"
    cap = prof.get("boost_speed_mps") if boost and prof.get("boost_speed_mps") else prof["flat_speed_mps"]
    if need <= 0:
        return cap, None  # downhill: the speed controller holds the cap
    power = prof["power_w"] * (prof.get("boost_power_factor", 1.0) if boost else 1.0)
    return min(cap, power / need), None
