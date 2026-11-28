"""Application factory and entry point."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from finance360.api import (
    account_identifiers,
    accounts,
    ask,
    audit,
    backup,
    catalogue,
    documents,
    due,
    exposure_profiles,
    fetch,
    fx_rates,
    health,
    household,
    imports,
    income_streams,
    instruments,
    library,
    look_through,
    position,
    preferences,
    prices,
    projection,
    questions,
    rule_figures,
    scenarios,
    super_statements,
    tax,
    tax_estimates,
    tax_lodged_results,
    transactions,
    valuations,
)
from finance360.api import auth as auth_api
from finance360.api import (
    document_profiles as document_profiles_api,
)
from finance360.api.deps import current_principal
from finance360.behaviours import REGISTRY
from finance360.capabilities.ai import providers as ai_providers
from finance360.capabilities.auth import csrf, hosts, logins, setup_code
from finance360.capabilities.auth.policy import utcnow
from finance360.capabilities.auth.redact import RedactFilter
from finance360.capabilities.auth.types import AuthProvider
from finance360.capabilities.documents import registry as document_profiles
from finance360.capabilities.documents import store as document_profile_store
from finance360.capabilities.household import questions as household_questions
from finance360.capabilities.household.service import (
    bootstrap,
    household_today,
    load_fixture_facts,
)
from finance360.capabilities.imports import account_identifiers as import_account_identifiers
from finance360.capabilities.imports import commit as import_commit
from finance360.capabilities.imports import registry as importers
from finance360.capabilities.library import profiles as library_profiles
from finance360.capabilities.library import registry as link_kinds
from finance360.capabilities.library import service as library_service
from finance360.capabilities.lookthrough import option_links as lookthrough_option_links
from finance360.capabilities.position import accounts as position_accounts
from finance360.capabilities.position import derived as position_derived
from finance360.capabilities.position import due as position_due
from finance360.capabilities.position import link_kinds as position_link_kinds
from finance360.capabilities.position import questions as position_questions
from finance360.capabilities.position import super_statements as position_super_statements
from finance360.capabilities.position import valuations as position_valuations
from finance360.capabilities.projection import income_streams as projection_income_streams
from finance360.capabilities.projection import questions as projection_questions
from finance360.capabilities.projection import scenarios as projection_scenarios
from finance360.capabilities.projection.seed import seed_assumptions
from finance360.capabilities.reference import exposure_profiles as reference_exposure_profiles
from finance360.capabilities.reference import fetch as reference_fetch
from finance360.capabilities.reference import fx_rates as reference_fx_rates
from finance360.capabilities.reference import instruments as reference_instruments
from finance360.capabilities.reference import prices as reference_prices
from finance360.capabilities.reference import providers as reference_providers
from finance360.capabilities.tax import due as tax_due
from finance360.capabilities.tax import fill_target as tax_fill_target
from finance360.capabilities.tax import link_kind as tax_link_kind
from finance360.capabilities.tax import lodged as tax_lodged
from finance360.capabilities.tax import questions as tax_questions
from finance360.capabilities.tax import rule_figures_due
from finance360.capabilities.tax import saved as tax_saved
from finance360.capabilities.tax import service as tax_service
from finance360.config import Settings
from finance360.core import backup as core_backup
from finance360.core import due as core_due
from finance360.core import questions as core_questions
from finance360.core import rulepack_store
from finance360.core.catalogue import check_behaviour_keys, seed_catalogue
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.exposure_vocab import seed_exposure_vocab
from finance360.core.migrate import migrate
from finance360.core.rulepack import register as register_rule_pack
from finance360.jobs.scheduler import backup_loop, fetch_datasets, fetch_loop
from finance360.plugins.ai_providers import FACTORIES as AI_PROVIDER_FACTORIES
from finance360.plugins.auth import REGISTRY as AUTH_PROVIDERS
from finance360.plugins.documents.ato_income_statement import PROFILE as ATO_INCOME_STATEMENT
from finance360.plugins.importers.vanguard_pi import IMPORTER as VANGUARD_PI
from finance360.plugins.providers import rba, yfinance
from finance360.rulepacks.au import PACK as AU_RULE_PACK

#: Loggers that could carry a request line or an error mentioning a credential (ADR-0010).
_REDACTED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "finance360")


def _redact_logs() -> None:
    for name in _REDACTED_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactFilter) for f in logger.filters):
            logger.addFilter(RedactFilter())


def _auth_providers(settings: Settings) -> list[AuthProvider]:
    unknown = [k for k in settings.auth_providers if k not in AUTH_PROVIDERS]
    if unknown or not settings.auth_providers:
        raise ValueError(
            f"FINANCE360_AUTH_PROVIDERS: unknown provider(s) {unknown or '(none given)'}; "
            f"known: {sorted(AUTH_PROVIDERS)}"
        )
    return [AUTH_PROVIDERS[k] for k in settings.auth_providers]


def _start_auth(session: Session, settings: Settings) -> bool:
    """Seed the owner Login if asked (never overwriting). True if the app still needs setup."""
    if settings.owner_password:
        try:
            logins.seed_owner(session, settings.owner_password, now=utcnow())
        except logins.PasswordPolicyError as problem:
            raise ValueError(f"FINANCE360_OWNER_PASSWORD(_FILE): {problem.message}") from None
    return logins.setup_required(session)


def _register_providers(settings: Settings) -> None:
    """Providers register here, the composition root (ADR-0013 section 6). ``sample`` transport
    swaps only the network seam for invented offline data; the real code still runs."""
    sample = settings.provider_transport == "sample"
    reference_providers.register_price_provider(yfinance.make_provider(sample=sample))
    reference_providers.register_fx_provider(rba.make_provider(sample=sample))


def _check_providers(settings: Settings) -> None:
    """A mistyped provider key fails loudly at start-up, like an unknown auth provider."""
    for var, key, known in (
        (
            "FINANCE360_PRICE_PROVIDER",
            settings.price_provider,
            reference_providers.price_provider_keys(),
        ),
        ("FINANCE360_FX_PROVIDER", settings.fx_provider, reference_providers.fx_provider_keys()),
    ):
        if key is not None and key not in known:
            raise ValueError(f"{var}: unknown provider {key!r}; known: {known}")


def _register_ai(settings: Settings) -> None:
    """The AI provider selected by ``FINANCE360_AI_PROVIDER`` registers here (ADR-0021). Unset is a
    quiet off; an unknown value fails loudly at start-up, like an unknown auth provider."""
    key = settings.ai.provider
    if key is None:
        return
    factory = AI_PROVIDER_FACTORIES.get(key)
    if factory is None:
        raise ValueError(
            f"FINANCE360_AI_PROVIDER: unknown provider {key!r}; "
            f"known: {sorted(AI_PROVIDER_FACTORIES)}"
        )
    ai_providers.register(factory(settings.ai.api_key))


def register_due_rules() -> None:
    """Due rules register here, the composition root, like Importers (ADR-0020 section 1): each
    owner's package contributes its own, and the ``due`` Capability imports none of them."""
    for rule in (*position_due.RULES, *tax_due.RULES, *rule_figures_due.RULES):
        core_due.register(rule)


def register_questions() -> None:
    """Questions and their inputs register here, the composition root, like Due rules (ADR-0032
    section 1): the Capability that answers a Question contributes it, the data's owner contributes
    each input, and the ``questions`` Capability imports none of them. Registry order is the order
    the cards show: what we own, where we are heading, this tax year."""
    for question_input in (
        *household_questions.INPUTS,
        *position_questions.INPUTS,
        *projection_questions.INPUTS,
        *tax_questions.INPUTS,
    ):
        core_questions.register_input(question_input)
    for question in (
        *position_questions.QUESTIONS,
        *projection_questions.QUESTIONS,
        *tax_questions.QUESTIONS,
    ):
        core_questions.register_question(question)


CallNext = Callable[[Request], Awaitable[Response]]


ProxyAddresses = Callable[[], frozenset[str]]


def create_app(
    settings: Settings | None = None, proxy_addresses: ProxyAddresses | None = None
) -> FastAPI:
    """``proxy_addresses`` answers "which peers are the proxy?"; tests inject it (ADR-0012)."""
    settings = settings or Settings.from_env()
    proxy_addresses = proxy_addresses or hosts.ProxyPeers()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # Held while serving, so a restore cannot replace the data under a running app.
        with core_backup.data_dir_lock(settings.data_dir):
            async with _serve(app):
                yield

    @asynccontextmanager
    async def _serve(app: FastAPI) -> AsyncGenerator[None]:
        migrate(settings.data_dir)  # creates the data dir and database; backs up first if needed
        app.state.engine = make_engine(db_path(settings.data_dir))
        app.state.session_factory = make_session_factory(app.state.engine)
        try:
            with app.state.session_factory.begin() as session:
                seed_catalogue(session)  # idempotent; inserts only missing Categories/types
                seed_exposure_vocab(session)  # idempotent; inserts only missing labels (ADR-0030)
                check_behaviour_keys(session, REGISTRY.keys())  # fail fast on a bad key
                # Evidence files moved from documents/tax/ to documents/library/ (ADR-0026):
                # idempotent, verified, and a bad file is logged, never dropped or fatal.
                library_service.adopt_files(session, settings.data_dir)
                # Saved Document profiles that are active register after the shipped ones
                # (ADR-0027 section 5). A bad row is skipped and shown as failed to load; it can
                # never stop the app starting.
                document_profile_store.load_active(session)
                bootstrap(session, settings.data_dir)  # first run: the Household (and fixture)
                if position_accounts.load_fixture_accounts(session, settings.data_dir):
                    load_fixture_facts(session, settings.data_dir)
                import_account_identifiers.load_fixture_account_identifiers(
                    session, settings.data_dir
                )
                position_valuations.load_fixture_valuations(session, settings.data_dir)
                position_super_statements.load_fixture_super_statements(session, settings.data_dir)
                seed_assumptions(session)  # idempotent; never overwrites an edited Assumption
                projection_scenarios.load_fixture_assumptions(session, settings.data_dir)
                projection_income_streams.load_fixture_income_streams(session, settings.data_dir)
                tax_service.load_fixture_tax_records(session, settings.data_dir)
                tax_saved.load_fixture_tax_whatifs(session, settings.data_dir)
                tax_lodged.load_fixture_lodged_results(session, settings.data_dir)
                library_service.load_fixture_documents(session, settings.data_dir)
                tax_service.load_fixture_tax_evidence(session, settings.data_dir)
                library_profiles.load_fixture_document_profiles(session, settings.data_dir)
                reference_fx_rates.load_fixture_fx_rates(session, settings.data_dir)
                reference_instruments.load_fixture_instruments(session, settings.data_dir)
                reference_prices.load_fixture_prices(session, settings.data_dir)
                reference_exposure_profiles.load_fixture_exposure_profiles(
                    session, settings.data_dir
                )
                lookthrough_option_links.load_fixture_option_links(session, settings.data_dir)
                rulepack_store.load_fixture_rule_figures(
                    session, settings.data_dir, today=household_today(session)
                )
                # Imports committed before unit prices were recorded have none; this fills them once
                import_commit.backfill_import_prices(session)
                needs_setup = _start_auth(session, settings)
                core_backup.fail_interrupted(session)  # a restart mid-run leaves a stale lock
                reference_fetch.fail_interrupted(session)
            if needs_setup:
                setup_code.ensure(settings.data_dir)  # never logged: read it on the host
            else:
                setup_code.remove(settings.data_dir)
            scheduler = (
                asyncio.create_task(backup_loop(settings, app.state.engine))
                if core_backup.configured(settings)
                else None
            )
            # The daily fetch exists only when a provider is configured: by default the instance
            # never touches the network for Prices or FX rates.
            fetcher = (
                asyncio.create_task(fetch_loop(settings, app.state.engine))
                if fetch_datasets(settings)
                else None
            )
            try:
                yield
            finally:
                for task in (scheduler, fetcher):
                    if task is not None:
                        task.cancel()
        finally:
            document_profiles.clear_saved()  # the registry is process-wide: leave no stale profile
            app.state.engine.dispose()

    # No /docs, /redoc or /openapi.json: default deny (ADR-0010). scripts/export_openapi.py calls
    # ``app.openapi()`` directly, so the generated client is unaffected.
    app = FastAPI(
        title="Finance360", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.auth_providers = _auth_providers(settings)
    _redact_logs()

    @app.middleware("http")
    async def refuse_cross_site(request: Request, call_next: CallNext) -> Response:
        # /health is exempt: the container healthcheck calls it as "localhost" (ADR-0012).
        if request.url.path != "/health":
            peer = request.client.host if request.client else None
            host = hosts.effective_host(request.headers, peer, proxy_addresses)
            if not hosts.host_allowed(host, settings.allowed_hosts):
                return JSONResponse({"message": "Host not allowed."}, status_code=421)
            if not csrf.same_origin(request.method, request.headers, host):
                return JSONResponse({"message": "Cross-site request refused."}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            # Data and auth responses are never cached: Back after sign-out must not show them.
            response.headers["Cache-Control"] = "no-store"
        return response

    position_accounts.register()  # position's Member delete guard (household never imports it)
    projection_income_streams.register()  # so does projection's, for income streams
    tax_service.register()  # and tax's, for Tax records
    tax_lodged.register()  # and for Lodged results
    importers.register(VANGUARD_PI)  # Importers register here, the composition root (ADR-0013)
    link_kinds.register(tax_link_kind.KIND)  # so do Link kinds (ADR-0026 section 3)
    link_kinds.register(tax_link_kind.LODGED_KIND)
    link_kinds.register(position_link_kinds.ACCOUNT_KIND)
    link_kinds.register(position_link_kinds.SUPER_STATEMENT_KIND)
    document_profiles.register_target(  # so do Fill targets (ADR-0026 section 4), before profiles
        document_profiles.FillTarget(
            key=tax_fill_target.KEY,
            title=tax_fill_target.TITLE,
            fields=tuple(document_profiles.TargetField(k, t) for k, t in tax_fill_target.FIELDS),
            has_period=tax_fill_target.HAS_PERIOD,
            link_kind=tax_fill_target.LINK_KIND,
        )
    )
    document_profiles.register(ATO_INCOME_STATEMENT)  # and Document profiles (ADR-0024)
    register_rule_pack(AU_RULE_PACK)  # so do Rule packs (ADR-0018 section 1)
    for behaviour in REGISTRY.values():  # so do Behaviours' derive hooks (capabilities never
        if behaviour.derive is not None:  # import behaviours/)
            position_derived.register_hook(behaviour.key, behaviour.derive)
    register_due_rules()
    register_questions()
    _register_providers(settings)
    _check_providers(settings)
    _register_ai(settings)
    app.include_router(health.router)
    app.include_router(auth_api.router)  # public: status, setup, login; the rest guard themselves
    # Default deny: every other API router requires a signed-in Principal (tests/test_route_guard).
    signed_in = [Depends(current_principal, scope="function")]
    app.include_router(household.router, dependencies=signed_in)
    app.include_router(catalogue.router, dependencies=signed_in)
    app.include_router(accounts.router, dependencies=signed_in)
    app.include_router(account_identifiers.router, dependencies=signed_in)
    app.include_router(valuations.router, dependencies=signed_in)
    app.include_router(super_statements.router, dependencies=signed_in)
    app.include_router(fx_rates.router, dependencies=signed_in)
    app.include_router(instruments.router, dependencies=signed_in)
    app.include_router(prices.router, dependencies=signed_in)
    app.include_router(exposure_profiles.router, dependencies=signed_in)
    app.include_router(look_through.router, dependencies=signed_in)
    app.include_router(fetch.router, dependencies=signed_in)
    app.include_router(imports.router, dependencies=signed_in)
    app.include_router(transactions.router, dependencies=signed_in)
    app.include_router(position.router, dependencies=signed_in)
    app.include_router(projection.router, dependencies=signed_in)
    app.include_router(income_streams.router, dependencies=signed_in)
    app.include_router(scenarios.router, dependencies=signed_in)
    app.include_router(tax.router, dependencies=signed_in)
    app.include_router(tax_estimates.router, dependencies=signed_in)
    app.include_router(tax_lodged_results.router, dependencies=signed_in)
    app.include_router(rule_figures.router, dependencies=signed_in)
    app.include_router(due.router, dependencies=signed_in)
    app.include_router(questions.router, dependencies=signed_in)
    app.include_router(preferences.router, dependencies=signed_in)
    app.include_router(documents.router, dependencies=signed_in)
    app.include_router(document_profiles_api.router, dependencies=signed_in)
    app.include_router(library.router, dependencies=signed_in)
    app.include_router(ask.router, dependencies=signed_in)
    app.include_router(audit.router, dependencies=signed_in)
    app.include_router(backup.router, dependencies=signed_in)  # owner-only inside
    household.register_error_handlers(app)
    accounts.register_error_handlers(app)

    static = settings.static_dir
    if static is not None and (static / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith(("health", "api/")):
                raise HTTPException(status_code=404)
            candidate = (static / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(static):
                return FileResponse(candidate)
            # The page names the build's hashed assets, so a browser must check it on every load:
            # without this it may reuse an old copy after an update and run the old code.
            return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def app() -> FastAPI:
    """uvicorn factory target: ``uvicorn finance360.main:app --factory``."""
    return create_app()


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port)  # noqa: S104


if __name__ == "__main__":
    run()
