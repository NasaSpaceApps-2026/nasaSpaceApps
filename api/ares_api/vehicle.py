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
    # Grip of the driven axle(s): uphill load transfer favours the rear on climbs, so use the
    # static share as a conservative estimate.
    share = prof.get("driven_load_share", 1.0)
    grip = prof["traction_mu"] * m * MARS_G * math.cos(th) * share
    if need >= grip:
        lim = math.degrees(math.atan(prof["traction_mu"] * share - prof["rolling_coeff"]))
        return 0.0, (f"wheels cannot grip on a {grade_deg:.0f} deg climb (driven-wheel grip runs out "
                     f"near {lim:.0f} deg)")
    cap = prof.get("boost_speed_mps") if boost and prof.get("boost_speed_mps") else prof["flat_speed_mps"]
    if need <= 0:
        return cap, None  # downhill: the speed controller holds the cap
    power = prof["power_w"] * (prof.get("boost_power_factor", 1.0) if boost else 1.0)
    return min(cap, power / need), None
