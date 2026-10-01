"""Emails about accessory requests (Parts & tools).

    requested            -> the HOD
    approved / declined  -> the requester (HOD copied, not the HOD who decided)
    received             -> the HOD

Called from the views where each step is taken, after the transaction
commits, so a step recorded while offline is queued on that PC and sent when
it is back online. Rows that arrive from other PCs through sync never call
these, so each step is mailed exactly once.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.urls import reverse

from .mailer import queue
from .recipients import hods

logger = logging.getLogger(__name__)


def _link():
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{base}{reverse('partstools:accessories_dashboard')}"


def _name(req):
    if req.request_type == "restock" and req.existing_accessory_id and req.existing_accessory.name:
        return req.existing_accessory.name.name
    return req.accessory_name or "accessory"


def _context(req, **extra):
    requester = req.requested_by.user if req.requested_by_id else None
    return {
        "req": req,
        "item": _name(req),
        "kind": "Restock" if req.request_type == "restock" else "New accessory",
        "equipment": req.equipment_description.name if req.equipment_description_id else "",
        "workshop": req.workshop.name if req.workshop_id else "",
        "requester": (requester.get_full_name() or requester.username) if requester else "",
        "total": req.unit_cost * req.requested_quantity,
        "link": _link(),
        **extra,
    }


def _later(fn, *args):
    def run():
        try:
            fn(*args)
        except Exception:  # mail must never break the request itself
            logger.exception("notifications: %s failed for accessory request %s", fn.__name__, args[0])
    transaction.on_commit(run)


def request_made(request_id, actor=None):
    _later(_request_made, request_id, actor)


def request_decided(request_id, actor=None):
    _later(_request_decided, request_id, actor)


def request_received(request_id, actor=None):
    _later(_request_received, request_id, actor)


def _load(request_id):
    from parts_tools.models import AccessoryRequest

    return AccessoryRequest.objects.select_related(
        "requested_by__user", "workshop", "equipment_description", "existing_accessory__name",
        "approved_by__user", "accepted_by__user", "created_accessory").get(pk=request_id)


def _request_made(request_id, actor):
    req = _load(request_id)
    ctx = _context(req, event="requested")
    queue(
        kind="accessory_requested", dedupe_key=f"accessory-request:{req.pk}:Pending",
        to_users=hods(), exclude=[actor],
        subject=f"[Action] {ctx['kind']} requested: {req.requested_quantity} × {ctx['item']} ({ctx['workshop']})",
        template="accessory_request", context=ctx,
        in_app_message=f"{ctx['requester']} requested {req.requested_quantity} × {ctx['item']} for {ctx['equipment']}.",
    )


def _request_decided(request_id, actor):
    req = _load(request_id)
    if req.status not in ("Approved", "Declined") or not req.requested_by_id:
        return
    decided_by = req.approved_by.user if req.approved_by_id else None
    ctx = _context(req, event=req.status.lower(),
                   decided_by=(decided_by.get_full_name() or decided_by.username) if decided_by else "the HOD")
    queue(
        kind="accessory_decided", dedupe_key=f"accessory-request:{req.pk}:{req.status}",
        to_users=[req.requested_by.user], exclude=[actor],
        subject=f"Your request for {ctx['item']} was {req.status.lower()}",
        template="accessory_request", context=ctx,
        in_app_message=(f"{req.status}: {req.requested_quantity} × {ctx['item']}"
                        + (f" — {req.approval_reason}" if req.approval_reason else "")),
    )


def _request_received(request_id, actor):
    req = _load(request_id)
    if req.status != "Accepted":
        return
    received_by = req.accepted_by.user if req.accepted_by_id else None
    stock = req.created_accessory.stock_count if req.created_accessory_id else None
    ctx = _context(req, event="received", stock=stock,
                   received_by=(received_by.get_full_name() or received_by.username) if received_by else "")
    queue(
        kind="accessory_received", dedupe_key=f"accessory-request:{req.pk}:Accepted",
        to_users=hods(), exclude=[actor],
        subject=f"Received: {req.requested_quantity} × {ctx['item']} ({ctx['workshop']})",
        template="accessory_request", context=ctx,
        in_app_message=f"{ctx['received_by']} received {req.requested_quantity} × {ctx['item']}"
                       + (f"; stock now {stock}." if stock is not None else "."),
    )
