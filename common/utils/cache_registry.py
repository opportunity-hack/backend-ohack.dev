"""Inventory of every in-process / Redis cache in the backend, with its class.

Why this exists: gunicorn runs 2 workers on each Fly machine (prod has 2
machines, staging 1) and there is NO Redis in either environment (verified
Nov 2026), so every cache below exists in up to 4 independent copies in prod.
A write clears only the copy in the worker that handled it; the others keep
serving the old value until their TTL expires. That is the bug class behind
"refresh alternates between two states" (BE #330).

Classes:

- ``static``           minutes of staleness are fine (news, lists, GitHub,
                       Slack lookups, PropelAuth identity). TTL unconstrained.
- ``read_after_write`` someone writes and expects to see it on the next load.
                       TTL MUST be <= READ_AFTER_WRITE_MAX_TTL seconds — the
                       cross-worker staleness window is the TTL, whatever the
                       write path clears.
- ``coordination``     a lock / job-status key stored with set_cached. Without
                       Redis these are per-process, so a status poll that lands
                       on another worker can't see the job. Known gap — listed
                       so it stays visible; needs Redis or Firestore to fix.
- ``infrastructure``   the cache machinery itself.

The guard test (test/common/test_cache_registry.py) scans the source tree with
``scan_caches()`` and fails when a cache is added, removed, or has its TTL
changed without updating CACHES below. Routes that show a person their OWN
state right after a write (own application/check-in status, own team, own
ballot, own teams on /profile) read Firestore uncached — don't add a cache
there; use ``read_after_write`` <= 10s if you must.

Deliberately import-light (stdlib only): the scanner parses source with ``ast``
and never imports the service modules.
"""
import ast
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

READ_AFTER_WRITE_MAX_TTL = 10

STATIC = "static"
READ_AFTER_WRITE = "read_after_write"
COORDINATION = "coordination"
INFRASTRUCTURE = "infrastructure"
CLASSES = (STATIC, READ_AFTER_WRITE, COORDINATION, INFRASTRUCTURE)

# kind: "cached" (cachetools @cached), "redis_cached" (@redis_cached),
# "ttlcache" (a named TTLCache object), "manual" (a set_cached(...) call; name
# is the enclosing function), "lru_cache" (functools).
KINDS = ("cached", "redis_cached", "ttlcache", "manual", "lru_cache")


@dataclass(frozen=True)
class CacheSpec:
    module: str
    name: str
    kind: str
    ttl: Optional[float]
    cls: str
    note: str = ""

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.module, self.name, self.kind)


def _c(module, name, kind, ttl, cls, note=""):
    return CacheSpec(module, name, kind, ttl, cls, note)


CACHES = (
    # --- infrastructure -----------------------------------------------------
    _c("common.utils.redis_cache", "local_cache", "ttlcache", 600, INFRASTRUCTURE,
       "Fallback store for @redis_cached when Redis is off; per-entry TTL is the caller's."),

    # --- read_after_write (<= 10s) -----------------------------------------
    _c("common.utils.firestore_helpers", "_doc_to_json_cached", "cached", 10, READ_AFTER_WRITE,
       "DocumentReference resolution (team/nonprofit docs inside events). Was 600s."),
    _c("services.hackathons_service", "get_single_hackathon_event", "cached", 10, READ_AFTER_WRITE,
       "Public event payload incl. teams + mentor fields. Was 600s."),
    _c("services.hackathons_service", "get_single_hackathon_id", "cached", 10, READ_AFTER_WRITE,
       "Was 20s."),
    _c("services.hackathons_service", "get_volunteer_by_event", "cached", 10, READ_AFTER_WRITE,
       "Admin/public rosters after approve/roster toggles."),
    _c("services.hackathons_service", "get_volunteer_checked_in_by_event", "cached", 5, READ_AFTER_WRITE,
       "Check-in desk counts."),
    _c("services.volunteers_service", "get_volunteer_by_user_id", "redis_cached", 2, READ_AFTER_WRITE),
    _c("services.volunteers_service", "get_volunteer_by_email", "redis_cached", 2, READ_AFTER_WRITE),
    _c("services.volunteers_service", "get_volunteers_by_event", "redis_cached", 2, READ_AFTER_WRITE),
    _c("services.users_service", "get_profile_metadata", "cached", 10, READ_AFTER_WRITE,
       "GET /api/users/profile — the caller's own profile. Was 600s."),
    _c("services.users_service", "get_portfolio_profile", "redis_cached", 10, READ_AFTER_WRITE,
       "Public portfolio; a public->private flip must take effect fast. Was 300s."),
    _c("api.messages.messages_service", "get_profile_metadata_old", "cached", 10, READ_AFTER_WRITE,
       "Legacy own-profile delegate. Was 600s."),
    _c("api.messages.messages_service", "get_user_by_id_old", "cached", 10, READ_AFTER_WRITE,
       "Legacy by-id profile delegate. Was 600s."),
    _c("api.messages.messages_service", "get_all_profiles", "cached", 10, READ_AFTER_WRITE,
       "Admin people search. Was 300s."),
    _c("api.judging.judging_service", "_PUBLIC_SCHEDULE_CACHE", "ttlcache", 10, READ_AFTER_WRITE,
       "Public judging schedule after publish/assignment/lock writes. Was 60s."),
    _c("api.judging.judging_service", "_build_public_schedule", "cached", 10, READ_AFTER_WRITE,
       "Backed by _PUBLIC_SCHEDULE_CACHE."),
    _c("api.jobs.jobs_service", "get_public_listings", "cached", 10, READ_AFTER_WRITE,
       "Admin publish/close. Was 300s."),
    _c("api.jobs.jobs_service", "get_public_listing", "cached", 10, READ_AFTER_WRITE, "Was 300s."),
    _c("services.problem_statements_service", "_helpers_cache", "ttlcache", 10, READ_AFTER_WRITE,
       "'Who's helping' roster refetched right after the help toggle. Was 60s."),

    # --- static --------------------------------------------------------------
    _c("api.slack.slack_service", "get_active_users", "redis_cached", 10, STATIC),
    _c("api.slack.slack_service", "get_user_details", "redis_cached", 10, STATIC),
    _c("services.teams_service", "_TEAM_USERS_CACHE", "ttlcache", 600, STATIC,
       "Keyed by the tuple of member ids, so join/leave is a new key; only names/avatars lag."),
    _c("api.teams.teams_service", "_PROJECT_OPTIONS_CACHE", "ttlcache", 60, STATIC,
       "Projects a team can rank at creation (GET /api/team/<event>/project-options); "
       "queue_team validates against the same cached copy so the page and API agree."),
    _c("services.hackathons_service", "get_hackathon_list", "cached", 3600, STATIC),
    _c("services.hackathons_service", "get_hackathon_funnel", "cached", 300, STATIC),
    _c("services.hackathons_service", "get_hackathon_funnel_aggregate", "manual", 6 * 3600, STATIC),
    _c("services.nonprofits_service", "_npo_list_cache", "ttlcache", 300, STATIC),
    _c("services.nonprofits_service", "get_npo_list", "cached", 300, STATIC,
       "Backed by _npo_list_cache."),
    _c("services.problem_statements_service", "_ps_list_cache", "ttlcache", 600, STATIC),
    _c("services.problem_statements_service", "get_problem_statements", "cached", 600, STATIC,
       "Backed by _ps_list_cache."),
    _c("services.problem_statements_service", "get_problem_statement", "cached", 600, STATIC,
       "Project page is ISR anyway."),
    _c("api.messages.messages_service", "get_single_problem_statement_old", "cached", 600, STATIC),
    _c("api.messages.messages_service", "get_github_profile", "cached", 3600, STATIC),
    _c("services.news_service", "get_news", "cached", 32600, STATIC),
    _c("services.news_service", "get_all_praises", "cached", 600, STATIC),
    _c("services.news_service", "get_praises_about_user", "cached", 600, STATIC),
    _c("services.feedback_service", "get_user_feedback", "cached", 600, STATIC,
       "Feedback is written by someone else; the recipient reads it later."),
    _c("services.users_service", "_PROPEL_CACHE", "ttlcache", 600, STATIC, "PropelAuth identity."),
    _c("services.users_service", "_PROPEL_MISS_CACHE", "ttlcache", 300, STATIC),
    _c("services.users_service", "_fetch_portfolio_teams_cached", "redis_cached", 900, STATIC),
    _c("services.users_service", "get_searchable_portfolio_sitemap", "redis_cached", 3600, STATIC),
    _c("services.planning_mention_notifier", "_get_oauth_user_cached", "cached", 300, STATIC),
    _c("common.utils.firebase", "get_github_contributions_for_user", "redis_cached", 3600, STATIC),
    _c("common.utils.slack", "userlist", "redis_cached", 600, STATIC),
    _c("common.utils.slack", "_EMAIL_USER_CACHE", "ttlcache", 86400, STATIC),
    _c("common.utils.slack", "_EMAIL_USER_MISS_CACHE", "ttlcache", 3600, STATIC),
    _c("common.utils.slack", "get_channel_id_from_channel_name", "cached", 60, STATIC),
    _c("common.utils.slack", "is_channel_id", "cached", 60, STATIC),
    _c("api.certificates.certificate_service", "get_certificates_by_github_username", "redis_cached", 900, STATIC),
    _c("api.github.github_service", "_ISSUES_CACHE", "ttlcache", 600, STATIC),
    _c("api.github.github_service", "_ACTIVITY_CACHE", "ttlcache", 300, STATIC),
    _c("api.leaderboard.leaderboard_service", "_MENTOR_OPPS_CACHE", "ttlcache", 300, STATIC),
    _c("api.leaderboard.leaderboard_service", "collect_mentor_panel_opportunities", "cached", 300, STATIC,
       "Backed by _MENTOR_OPPS_CACHE."),
    _c("api.leaderboard.leaderboard_service", "get_github_leaderboard", "manual", 300, STATIC),
    _c("services.broadcasts_service", "list_contacts", "manual", 60, STATIC,
       "Resend contact list (admin; ?force=true busts)."),
    _c("services.volunteers_service", "_fetch_one_status", "manual", None, STATIC,
       "Resend delivery status per email id (ttl computed per status)."),
    _c("services.volunteers_service", "_fetch_and_cache_resend_emails", "manual", 3600, STATIC,
       "Resend email index for the admin volunteer table."),

    # --- coordination (known gap without Redis) ------------------------------
    _c("services.broadcasts_service", "start_contact_prune", "manual", 1800, COORDINATION,
       "Prune lock + status are per-process without Redis: a status poll on another "
       "worker can't see the job, and two workers could each start one."),
    _c("services.broadcasts_service", "_update_prune_status", "manual", 86400, COORDINATION),
    _c("services.broadcasts_service", "start_segment_sync", "manual", 1800, COORDINATION,
       "Same gap for segment sync."),
    _c("services.broadcasts_service", "_update_sync_status", "manual", 86400, COORDINATION),
    _c("services.volunteers_service", "_maybe_start_background", "manual", 120, COORDINATION,
       "Resend-index refresh lock; per-process, so each worker may refresh once."),
)


# ---------------------------------------------------------------------------
# Source scanner (used by the guard test; also handy from a REPL)
# ---------------------------------------------------------------------------

_SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", "test", "tests",
              "scripts", ".claude", "migrations"}
_SELF = "common.utils.cache_registry"


def _func_name(node) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal(node, consts):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Add)):
        left, right = _literal(node.left, consts), _literal(node.right, consts)
        if left is None or right is None:
            return None
        return left * right if isinstance(node.op, ast.Mult) else left + right
    return None


def _arg(call: ast.Call, name: str, pos: Optional[int] = None):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    if pos is not None and len(call.args) > pos:
        return call.args[pos]
    return None


def _ttlcache_ttl(call: ast.Call, consts):
    return _literal(_arg(call, "ttl", 1), consts)


def _module_name(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)[:-3]
    parts = rel.split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def scan_caches(root: str) -> Dict[Tuple[str, str, str], Optional[float]]:
    """{(module, name, kind): ttl} for every cache found in the source tree."""
    found: Dict[Tuple[str, str, str], Optional[float]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("wt-")]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            module = _module_name(root, path)
            if module == _SELF:
                continue
            with open(path, encoding="utf-8") as fh:
                try:
                    tree = ast.parse(fh.read(), filename=path)
                except SyntaxError:
                    continue
            _scan_module(tree, module, found)
    return found


def _scan_module(tree, module, found):
    consts = {}
    ttlcache_vars = {}
    for node in tree.body:
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            lit = _literal(value, consts)
            if lit is not None:
                consts[target.id] = lit

    claimed = set()  # TTLCache Call nodes accounted for (decorator / assignment)

    for node in ast.walk(tree):
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if isinstance(value, ast.Call) and _func_name(value.func) == "TTLCache":
            for target in targets:
                if isinstance(target, ast.Name):
                    ttl = _ttlcache_ttl(value, consts)
                    ttlcache_vars[target.id] = ttl
                    found[(module, target.id, "ttlcache")] = ttl
                    claimed.add(id(value))

    def visit(node, enclosing):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in child.decorator_list:
                    call = dec if isinstance(dec, ast.Call) else None
                    name = _func_name(call.func if call else dec)
                    if name == "cached" and call is not None:
                        cache = _arg(call, "cache", 0)
                        ttl = None
                        if isinstance(cache, ast.Call) and _func_name(cache.func) == "TTLCache":
                            ttl = _ttlcache_ttl(cache, consts)
                            claimed.add(id(cache))
                        elif isinstance(cache, ast.Name):
                            ttl = ttlcache_vars.get(cache.id)
                        found[(module, child.name, "cached")] = ttl
                    elif name == "redis_cached":
                        ttl_node = _arg(call, "ttl", 1) if call is not None else None
                        ttl = 600 if ttl_node is None else _literal(ttl_node, consts)
                        found[(module, child.name, "redis_cached")] = ttl
                    elif name == "lru_cache":
                        found[(module, child.name, "lru_cache")] = None
                visit(child, child.name)
            else:
                if (isinstance(child, ast.Call) and _func_name(child.func) == "set_cached"
                        and module != "common.utils.redis_cache"):
                    ttl = _literal(_arg(child, "ttl", 2), consts)
                    key = (module, enclosing or "<module>", "manual")
                    if key not in found or found[key] is None:
                        found[key] = ttl
                if (isinstance(child, ast.Call) and _func_name(child.func) == "TTLCache"
                        and id(child) not in claimed):
                    found[(module, f"<unnamed TTLCache in {enclosing or 'module'}>", "ttlcache")] = (
                        _ttlcache_ttl(child, consts))
                visit(child, enclosing)

    visit(tree, None)
