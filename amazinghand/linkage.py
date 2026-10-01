from __future__ import annotations

import numpy as np


class ConstrainedHandKinematics:
    """Close the reference MJCF linkages while holding all eight motors fixed.

    ``mj_forward`` evaluates a configuration; it does not solve its closed loops.
    Like the demo's Mink equality task, this solver uses the MJCF equality errors
    and Jacobians. Here the inputs are motor angles, so only passive DOFs move.
    Ball joints are integrated in tangent space to keep valid quaternions.
    """

    CLOSURE_TOLERANCE = 1e-7  # metres; the CAD export has small rounding errors
    MAX_MOTOR_STEP = 0.1  # radians, to stay on the same linkage assembly branch

    def __init__(self, model, data):
        import mujoco

        self.mujoco = mujoco
        self.model = model
        self.data = data
        self.motor_names = tuple(
            f"finger{finger}_motor{motor}"
            for finger in range(1, 5) for motor in (1, 2)
        )
        joints = [model.joint(name).id for name in self.motor_names]
        self._addresses = model.jnt_qposadr[joints].copy()
        self._actuators = np.array([model.actuator(name).id for name in self.motor_names])
        self._passive_dofs = np.setdiff1d(np.arange(model.nv), model.jnt_dofadr[joints])
        self._ranges = np.full((len(joints), 2), [-np.inf, np.inf])
        for index, (joint, actuator) in enumerate(zip(joints, self._actuators)):
            if model.jnt_limited[joint]:
                self._ranges[index] = model.jnt_range[joint]
            if model.actuator_ctrllimited[actuator]:
                self._ranges[index, 0] = max(self._ranges[index, 0], model.actuator_ctrlrange[actuator, 0])
                self._ranges[index, 1] = min(self._ranges[index, 1], model.actuator_ctrlrange[actuator, 1])
        # This small model has 56 DOFs. A dense Jacobian also avoids relying on
        # MuJoCo's automatic dense/sparse selection when reading efc_J.
        model.opt.jacobian = mujoco.mjtJacobian.mjJAC_DENSE
        self._last_target: np.ndarray | None = None

    def set_motor_positions(self, positions_radians: dict[str, float]) -> None:
        target = self.data.qpos[self._addresses].copy()
        for name, value in positions_radians.items():
            if name not in self.motor_names:
                raise ValueError(f"Unknown hand motor: {name}")
            if not np.isfinite(value):
                raise ValueError(f"Motor angle must be finite: {name}")
            target[self.motor_names.index(name)] = float(value)
        target = np.clip(target, self._ranges[:, 0], self._ranges[:, 1])
        if self._last_target is not None and np.array_equal(target, self._last_target):
            return

        previous = self.data.qpos.copy()
        start = previous[self._addresses]
        steps = max(1, int(np.ceil(np.max(np.abs(target - start)) / self.MAX_MOTOR_STEP)))
        try:
            for step in range(1, steps + 1):
                self.data.qpos[self._addresses] = start + (target - start) * (step / steps)
                self._close_linkages()
        except Exception:
            self.data.qpos[:] = previous
            self.mujoco.mj_forward(self.model, self.data)
            raise
        self.data.ctrl[self._actuators] = target
        self.data.qvel[:] = 0
        self.data.qacc[:] = 0
        self.mujoco.mj_forward(self.model, self.data)
        self._last_target = target.copy()

    def _equality_error(self) -> np.ndarray:
        mask = self.data.efc_type == self.mujoco.mjtConstraint.mjCNSTR_EQUALITY
        return self.data.efc_pos[mask].copy()

    def _close_linkages(self) -> None:
        mujoco, model, data = self.mujoco, self.model, self.data
        mujoco.mj_forward(model, data)
        for _ in range(40):
            error = self._equality_error()
            if error.size == 0:
                raise RuntimeError("The hand model has no active linkage constraints.")
            if np.max(np.abs(error)) <= self.CLOSURE_TOLERANCE:
                return
            mask = data.efc_type == mujoco.mjtConstraint.mjCNSTR_EQUALITY
            jacobian = data.efc_J.reshape(data.nefc, model.nv)[mask][:, self._passive_dofs]
            delta = np.linalg.lstsq(jacobian, -error, rcond=1e-8)[0]
            if not np.all(np.isfinite(delta)) or np.max(np.abs(delta)) < 1e-10:
                break
            delta *= min(1.0, 0.3 / np.max(np.abs(delta)))
            velocity = np.zeros(model.nv)
            velocity[self._passive_dofs] = delta
            previous = data.qpos.copy()
            # Backtracking prevents a Newton step from opening a different loop.
            for scale in (1.0, 0.5, 0.25, 0.125, 0.0625):
                data.qpos[:] = previous
                mujoco.mj_integratePos(model, data.qpos, velocity, scale)
                mujoco.mj_forward(model, data)
                if np.linalg.norm(self._equality_error()) < np.linalg.norm(error):
                    break
            else:
                data.qpos[:] = previous
                mujoco.mj_forward(model, data)
                break
        error = float(np.max(np.abs(self._equality_error())))
        if error > self.CLOSURE_TOLERANCE or not np.isfinite(error):
            raise RuntimeError(f"Unable to close hand linkage at this pose (error {error:.3g} m).")
