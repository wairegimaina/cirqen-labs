"""Per-request database query budget (IMPROVEMENT_PLAN.md 5.5).

Development-only guard against N+1 queries. When settings.QUERY_BUDGET is a
number, every request counts its queries; one that goes over the budget is
logged with its path and count, and with QUERY_BUDGET_STRICT it raises, so the
offending page fails loudly in development and in the budget test instead of
silently getting slower as data grows.

Counting uses connection.execute_wrapper, so it works with DEBUG off and costs
nothing when QUERY_BUDGET is unset (the middleware is then not installed).
"""
import logging
from contextlib import ExitStack

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.db import connections

logger = logging.getLogger("cirqen.query_budget")


class QueryBudgetExceeded(AssertionError):
    pass


class QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)


class QueryBudgetMiddleware:
    def __init__(self, get_response):
        self.budget = getattr(settings, "QUERY_BUDGET", None)
        if not self.budget:
            raise MiddlewareNotUsed
        self.strict = getattr(settings, "QUERY_BUDGET_STRICT", False)
        self.get_response = get_response

    def __call__(self, request):
        counter = QueryCounter()
        with ExitStack() as stack:
            for alias in connections:
                stack.enter_context(connections[alias].execute_wrapper(counter))
            response = self.get_response(request)
        if counter.count > self.budget:
            message = f"{request.method} {request.path} ran {counter.count} queries (budget {self.budget})"
            logger.warning(message)
            if self.strict:
                raise QueryBudgetExceeded(message)
        response["X-Query-Count"] = str(counter.count)
        return response
