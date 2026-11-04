# Zenas testing sheet

This sheet currently covers the paid-step change. Existing product acceptance remains in its existing tests and product documentation.

## Paid ladder approval

Use a disposable test tenant and the cloud-side authenticated Tank client; do not put service keys into a browser. Configure an existing paid model on `zenas.apply` for this fixture. The screen switches await the frames listed in `docs/paid-steps/FRAMES.md`; the authenticated settings API is available now.

| Row | Where | What the founder does | Expected result | Setting it needs | Offline case |
| --- | --- | --- | --- | --- | --- |
| PAID-ZENAS | Tank `POST /v1/route`, task_type `zenas.apply`; existing `/chats` confirmation card | Send a test request in Ask mode. Confirm its pending paid-step card, then repeat the same run. Set that exact step to Auto and start a new run. Enable Pause all paid work and start another run. | Ask returns 409 with `paid_step_approval_required` before dispatch. Approval resumes with HTTP 200 and receipt `paid_step_mode=approved`; repeating the run does not dispatch again. Auto returns 200 with `paid_step_mode=automatic`. Master pause returns 503 and sends no paid call. | `paid_step_settings.mode`; `paid_paused`; existing configured model and room credentials | `tests/acceptance/test_paid_steps.py`, `zenas` |
