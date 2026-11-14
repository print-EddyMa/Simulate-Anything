"""Simulating something that is not in the catalogue.

A new simulation is one class: say how to solve it at resolution n and which
laws the answer must obey.  The base class then runs the whole self-check
protocol (units, well-posedness, residual, 3-grid convergence, known solution,
conservation, symmetry) for you.

Here: a bare wire carrying current, cooled by air, clamped at both ends.

    k A T'' + I^2 rho_e / A - h P (T - T_inf) = 0,   T(0) = T(L) = T_end

    python examples/custom_simulation.py
"""
import math

from simulate_anything import Field, Param, Q, Simulation, Solution, register
from simulate_anything import checks as ck
from simulate_anything.sims._heat import heat_checks, to_c
from simulate_anything.solvers.transport import Grid, TransportProblem, dirichlet


@register
class JouleHeatedWire(Simulation):
    name = "joule_heated_wire"
    title = "Current-carrying wire with convective cooling"
    domain = "electronics"
    description = "Steady temperature along a wire heated by its own resistance."
    params = [
        Param("length", "100 mm", "m", "wire length between the clamps"),
        Param("diameter", "0.5 mm", "m", "wire diameter"),
        Param("current", "4 A", "A", "current"),
        Param("resistivity", "1.7e-8 ohm*m", "ohm*m", "electrical resistivity (copper)"),
        Param("conductivity", "390 W/(m*K)", "W/(m*K)", "thermal conductivity"),
        Param("h", "30 W/(m^2*K)", "W/(m^2*K)", "convection coefficient"),
        Param("ambient_temperature", "25 degC", "K", "air temperature"),
        Param("end_temperature", "25 degC", "K", "clamp temperature"),
    ]
    primary = ("max_temperature",)

    def _numbers(self):
        p = self.p
        area = math.pi * p.diameter ** 2 / 4
        perim = math.pi * p.diameter
        heat = p.current ** 2 * p.resistivity / area ** 2        # W/m^3
        return area, perim, heat, p.h * perim / area

    # units have to work out: every term of the equation in W/m^3
    def equations(self):
        area = Q(self._numbers()[0], "m^2")
        return {"wire energy balance": {
            "conduction": self.q("conductivity") * Q(1, "K") / self.q("length") ** 2,
            "Joule heating": self.q("current") ** 2 * self.q("resistivity") / area ** 2,
            "convection": self.q("h") * Q(1, "K") / self.q("diameter")}}

    def _problem(self, n):
        p = self.p
        area, perim, heat, hv = self._numbers()
        return TransportProblem(
            Grid(n, p.length, ly=area), p.conductivity, sink=hv,
            source=heat + hv * p.ambient_temperature,
            bcs={"west": dirichlet(p.end_temperature), "east": dirichlet(p.end_temperature)})

    def well_posed(self):
        return self._problem(8).well_posed(what="temperature")

    def solve(self, n):
        ts = self._problem(n).solve_steady()
        g = ts.grid
        qoi = {"max_temperature": (to_c(ts.sample(self.p.length / 2)), "degC"),
               "heat_generated": (ts.source_total() - self._numbers()[3]
                                  * self.p.ambient_temperature * g.vol.sum(), "W")}
        fields = [Field("temperature along the wire", g.x * 1e3, ts.phi[:, 0] - 273.15,
                        unit="degC", xlabel="x [mm]", ylabel="T [degC]")]
        return Solution(qoi, fields, ts.residual, data={"ts": ts})

    # textbook answer: theta = theta_inf + (theta_0 - theta_inf) cosh(m x') / cosh(m L / 2)
    def known_solution(self, sol):
        p = self.p
        area, perim, heat, hv = self._numbers()
        m = math.sqrt(hv / p.conductivity)
        far = p.ambient_temperature + heat / hv              # infinitely long wire
        mid = far + (p.end_temperature - far) / math.cosh(m * p.length / 2)
        return [ck.close("known solution: mid-span temperature", ck.UNIVERSAL,
                         sol.qoi["max_temperature"][0], to_c(mid), 0, 0.02,
                         detail="exact cosh solution", unit="degC")]

    def domain_checks(self, sol):
        p = self.p
        _, _, heat, hv = self._numbers()
        return heat_checks(sol.data["ts"], lo=min(p.ambient_temperature, p.end_temperature),
                           generation=heat, loss=(hv, p.ambient_temperature),
                           why="heating only: nothing is colder than the surroundings")

    def symmetry_checks(self, sol):
        return sol.data["ts"].symmetry_checks()


if __name__ == "__main__":
    print(JouleHeatedWire().run().to_text())
    print()
    print(JouleHeatedWire(current="12 A").run().to_text())
