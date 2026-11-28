import {
  Link,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  type RouterHistory,
} from "@tanstack/react-router";

import { AiLogPage } from "@/components/ai-log-page";
import { AskButton, AskPanel, AskProvider } from "@/components/ask-panel";
import { AccountMenu } from "@/components/account-menu";
import { AccountDetailPage } from "@/components/account-detail-page";
import { SuperStatementDetailPage } from "@/components/super-statement-detail-page";
import { AccountsPage } from "@/components/accounts-page";
import { AppShell } from "@/components/app-shell";
import { AuditPage } from "@/components/audit-page";
import { BackupStatus } from "@/components/backup-status";
import { CategoriesPage } from "@/components/categories-page";
import { DocumentProfilePage, ShippedProfilePage } from "@/components/document-profile-page";
import { DocumentProfilesPage } from "@/components/document-profiles-page";
import { FetchRunDetailPage } from "@/components/fetch-run-detail-page";
import { FxRatesPage } from "@/components/fx-rates-page";
import { GuidePage } from "@/components/guide-page";
import { HealthStatus } from "@/components/health-status";
import { HouseholdPage } from "@/components/household-page";
import { ImportDetailPage } from "@/components/import-detail-page";
import { ImportsPage } from "@/components/imports-page";
import { IncomeStreamDetailPage } from "@/components/income-stream-detail-page";
import { IncomeStreamsPage } from "@/components/income-streams-page";
import { InstrumentDetailPage } from "@/components/instrument-detail-page";
import { InstrumentsPage } from "@/components/instruments-page";
import { LibraryDocumentPage } from "@/components/library-document-page";
import { LibraryPage } from "@/components/library-page";
import { NoticeToaster } from "@/components/notice-toast";
import { RecentChangesPage } from "@/components/recent-changes";
import { PasteProfilePage } from "@/components/paste-profile-page";
import { ExposurePage } from "@/components/exposure-page";
import { PositionPage } from "@/components/position-page";
import { QuestionPage } from "@/components/question-page";
import { ProjectionPage, parseProjectionSearch } from "@/components/projection-page";
import { ReferenceDataPage } from "@/components/reference-data-page";
import { RuleFiguresPage } from "@/components/rule-figures-page";
import { ComparePage } from "@/components/compare-page";
import { SavedRunPage, SavedRunsPage } from "@/components/saved-runs-page";
import { SettingsPage } from "@/components/settings-page";
import { SidebarNav } from "@/components/sidebar-nav";
import { IncomeReconciliationPage } from "@/components/income-reconciliation-page";
import { HomePage } from "@/components/home-page";
import { TaxComparePage, TaxEstimatePage, TaxEstimatesPage } from "@/components/tax-estimates-page";
import { TaxLodgedPage } from "@/components/tax-lodged-page";
import { TaxPage } from "@/components/tax-page";
import { TaxRecordDetailPage } from "@/components/tax-record-detail-page";
import { TaxRecordsPage } from "@/components/tax-records-page";
import { TaxWhatIfPage, TaxWhatIfsPage } from "@/components/tax-whatifs-page";
import { buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { parseAuditSearch } from "@/lib/audit";
import { FROM_PARAM } from "@/lib/due";
import { parseMarker } from "@/lib/return-bar";
import { parseLibrarySearch } from "@/lib/library";
import { parseRuleFiguresSearch } from "@/lib/rule-figures";
import { parseTaxRecordsSearch, parseTaxSearch } from "@/lib/tax";
import { parseCompareSearch, parseYearSearch } from "@/lib/tax-estimates";
import { parseLodgedSearch } from "@/lib/tax-lodged";
import { parseExposureSearch } from "@/lib/exposure-rollup";
import { parsePositionSearch } from "@/lib/position-search";
import { parseTransactionsSearch } from "@/lib/transactions";

function RootLayout() {
  return (
    <AskProvider>
      <AppShell
        nav={<SidebarNav />}
        ask={<AskButton />}
        dock={<AskPanel />}
        actions={<AccountMenu />}
        footer={
          <div className="flex flex-col gap-1">
            <HealthStatus />
            <BackupStatus />
          </div>
        }
      >
        <Outlet />
        <NoticeToaster />
      </AppShell>
    </AskProvider>
  );
}

function NotFound() {
  return (
    <div role="alert">
      <PageHeader title="Page not found" description="There is nothing at this address." />
      <Link to="/position" activeOptions={{ exact: true }} className={buttonVariants({ variant: "outline" })}>
        Back to Position
      </Link>
    </div>
  );
}

/**
 * The root route declares the return marker once (`?from=due` or `?from=q.<key>`), so every screen
 * accepts it and the shell can read it; no destination route declares it.
 */
const rootRoute = createRootRoute({
  component: RootLayout,
  notFoundComponent: NotFound,
  validateSearch: (search: Record<string, unknown>) => ({
    [FROM_PARAM]: parseMarker(search[FROM_PARAM]),
  }),
});

const route = (path: string, component: () => React.ReactNode) =>
  createRoute({ getParentRoute: () => rootRoute, path, component });

/** Home: net Position above What's due (ADR-0032). */
const indexRoute = route("/", HomePage);

/** Recent changes: every Notice, dismissed ones muted (ADR-0033). */
const recentChangesRoute = route("/recent-changes", RecentChangesPage);

/** One Question: `/questions/own-and-owe` (ADR-0032). */
const questionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/questions/$questionKey",
  component: function QuestionRoute() {
    const { questionKey } = questionRoute.useParams();
    return <QuestionPage questionKey={questionKey} />;
  },
});

/** The chosen Scenario is a URL search param: `/projection?scenario=2`. */
const projectionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/projection",
  validateSearch: (search: Record<string, unknown>) => parseProjectionSearch(search),
  component: function ProjectionRoute() {
    const search = projectionRoute.useSearch();
    const navigate = projectionRoute.useNavigate();
    return <ProjectionPage search={search} onSelect={(scenario) => void navigate({ search: { scenario } })} />;
  },
});

/** The financial year is a URL search param: `/tax?fy=2027`. */
const taxRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax",
  validateSearch: (search: Record<string, unknown>) => parseTaxSearch(search),
  component: function TaxRoute() {
    const { fy } = taxRoute.useSearch();
    const navigate = taxRoute.useNavigate();
    return <TaxPage fy={fy} onYear={(year) => void navigate({ search: { fy: year }, replace: true })} />;
  },
});

/** `/rule-figures?fy=2027&missing=1`: the financial year and the Missing-only filter are URL search params. */
const ruleFiguresRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/rule-figures",
  validateSearch: (search: Record<string, unknown>) => parseRuleFiguresSearch(search),
  component: function RuleFiguresRoute() {
    const { fy, missing } = ruleFiguresRoute.useSearch();
    const navigate = ruleFiguresRoute.useNavigate();
    return (
      <RuleFiguresPage
        fy={fy}
        missingOnly={missing === true}
        onSearch={(search) => void navigate({ search, replace: true })}
      />
    );
  },
});

/** `/tax/reconciliation?fy=2026`: the financial year is a URL search param. */
const incomeReconciliationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax/reconciliation",
  validateSearch: (search: Record<string, unknown>) => parseTaxSearch(search),
  component: function IncomeReconciliationRoute() {
    const { fy } = incomeReconciliationRoute.useSearch();
    const navigate = incomeReconciliationRoute.useNavigate();
    return <IncomeReconciliationPage fy={fy} onYear={(year) => void navigate({ search: { fy: year }, replace: true })} />;
  },
});

/** The register's filters are URL search params: `/tax/records?member=1&fy=2026&label=salary&q=...`. */
const taxRecordsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax/records",
  validateSearch: (search: Record<string, unknown>) => parseTaxRecordsSearch(search),
  component: function TaxRecordsRoute() {
    const filters = taxRecordsRoute.useSearch();
    const navigate = taxRecordsRoute.useNavigate();
    return (
      <TaxRecordsPage
        filters={filters}
        onFilters={(patch) =>
          void navigate({
            search: (prev: Record<string, unknown>) => parseTaxRecordsSearch({ ...parseTaxRecordsSearch(prev), ...patch }),
            replace: true,
          })
        }
      />
    );
  },
});

const taxRecordDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax/records/$recordId",
  component: function TaxRecordDetailRoute() {
    const { recordId } = taxRecordDetailRoute.useParams();
    return <TaxRecordDetailPage recordId={recordId} />;
  },
});

/** `/tax/whatifs?fy=2026`: the Tax what-ifs of a year. The year is a URL search param. */
const taxWhatIfsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax/whatifs",
  validateSearch: (search: Record<string, unknown>) => parseYearSearch(search),
  component: function TaxWhatIfsRoute() {
    const { fy } = taxWhatIfsRoute.useSearch();
    const navigate = taxWhatIfsRoute.useNavigate();
    return <TaxWhatIfsPage fy={fy} onYear={(year) => void navigate({ search: year ? { fy: year } : {}, replace: true })} />;
  },
});

const taxWhatIfRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax/whatifs/$whatifId",
  component: function TaxWhatIfRoute() {
    const { whatifId } = taxWhatIfRoute.useParams();
    return <TaxWhatIfPage whatifId={whatifId} />;
  },
});

/** `/tax/estimates?fy=2026`: the saved estimates of a year. The year is a URL search param. */
const taxEstimatesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax/estimates",
  validateSearch: (search: Record<string, unknown>) => parseYearSearch(search),
  component: function TaxEstimatesRoute() {
    const { fy } = taxEstimatesRoute.useSearch();
    const navigate = taxEstimatesRoute.useNavigate();
    return <TaxEstimatesPage fy={fy} onYear={(year) => void navigate({ search: year ? { fy: year } : {}, replace: true })} />;
  },
});

/** `/tax/estimates/compare?a=1&b=2`: the two saved estimates are URL search params. */
const taxCompareRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax/estimates/compare",
  validateSearch: (search: Record<string, unknown>) => parseCompareSearch(search),
  component: function TaxCompareRoute() {
    const { a, b } = taxCompareRoute.useSearch();
    return <TaxComparePage a={a} b={b} />;
  },
});

/** `/tax/lodged?member=1&fy=2026`: the return worksheet. The Member and year are URL search params. */
const taxLodgedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax/lodged",
  validateSearch: (search: Record<string, unknown>) => parseLodgedSearch(search),
  component: function TaxLodgedRoute() {
    const { member, fy } = taxLodgedRoute.useSearch();
    const navigate = taxLodgedRoute.useNavigate();
    return <TaxLodgedPage member={member} fy={fy} onSearch={(search) => void navigate({ search, replace: true })} />;
  },
});

const taxEstimateRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tax/estimates/$estimateId",
  component: function TaxEstimateRoute() {
    const { estimateId } = taxEstimateRoute.useParams();
    return <TaxEstimatePage estimateId={estimateId} />;
  },
});

/** The Library's filters and page are URL search params: `/library?q=statement&type=application/pdf&unlinked=1&page=2`. */
const libraryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/library",
  validateSearch: (search: Record<string, unknown>) => parseLibrarySearch(search),
  component: function LibraryRoute() {
    const search = libraryRoute.useSearch();
    const navigate = libraryRoute.useNavigate();
    return (
      <LibraryPage
        search={search}
        onSearch={(patch) =>
          void navigate({
            search: (prev: Record<string, unknown>) => parseLibrarySearch({ ...parseLibrarySearch(prev), ...patch }),
            replace: true,
          })
        }
      />
    );
  },
});

const libraryDocumentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/library/$documentId",
  component: function LibraryDocumentRoute() {
    const { documentId } = libraryDocumentRoute.useParams();
    return <LibraryDocumentPage documentId={documentId} />;
  },
});

const savedRunDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/projection/runs/$runId",
  component: function SavedRunRoute() {
    const { runId } = savedRunDetailRoute.useParams();
    return <SavedRunPage runId={runId} />;
  },
});

/** Audit filters are URL search params: `/audit?entity_type=&entity_id=&account_id=&tab=&...`. */
const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/audit",
  validateSearch: (search: Record<string, unknown>) => parseAuditSearch(search),
  component: function AuditRoute() {
    const search = auditRoute.useSearch();
    const navigate = auditRoute.useNavigate();
    return <AuditPage search={search} onSearch={(update) => void navigate({ search: (prev: Record<string, unknown>) => update(parseAuditSearch(prev)) })} />;
  },
});

/** Position state (as-at date, view, Tag, layout) is in the URL, so views are linkable. */
const positionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/position",
  validateSearch: (search: Record<string, unknown>) => parsePositionSearch(search),
  component: function PositionRoute() {
    const search = positionRoute.useSearch();
    const navigate = positionRoute.useNavigate();
    return (
      <PositionPage
        search={search}
        onSearch={(patch) =>
          void navigate({
            search: (prev: Record<string, unknown>) =>
              parsePositionSearch({ ...parsePositionSearch(prev), ...patch }),
            replace: true,
          })
        }
      />
    );
  },
});

/** Exposure state (Dimension, level, as-at date) is in the URL, so views are linkable. */
const exposureRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/exposure",
  validateSearch: (search: Record<string, unknown>) => parseExposureSearch(search),
  component: function ExposureRoute() {
    const search = exposureRoute.useSearch();
    const navigate = exposureRoute.useNavigate();
    return (
      <ExposurePage
        search={search}
        onSearch={(patch) =>
          void navigate({
            search: (prev: Record<string, unknown>) => parseExposureSearch({ ...parseExposureSearch(prev), ...patch }),
            replace: true,
            resetScroll: false,
          })
        }
      />
    );
  },
});

const accountsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/accounts",
  validateSearch: (search: Record<string, unknown>) => ({
    closed: search.closed === true || search.closed === "true" ? (true as const) : undefined,
  }),
  component: function AccountsRoute() {
    const { closed } = accountsRoute.useSearch();
    const navigate = accountsRoute.useNavigate();
    return (
      <AccountsPage
        showClosed={!!closed}
        onShowClosedChange={(show) =>
          void navigate({ search: show ? { closed: true } : {}, replace: true })
        }
      />
    );
  },
});

/** `/accounts/$accountId?stream=cash&txpage=2&superseded=true`: the Transactions view is linkable. */
const superStatementRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/accounts/$accountId/super-statements/$statementId",
  component: function SuperStatementRoute() {
    const { accountId, statementId } = superStatementRoute.useParams();
    return <SuperStatementDetailPage accountId={accountId} statementId={statementId} />;
  },
});

const accountDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/accounts/$accountId",
  validateSearch: (search: Record<string, unknown>) => parseTransactionsSearch(search),
  component: function AccountDetailRoute() {
    const { accountId } = accountDetailRoute.useParams();
    const search = accountDetailRoute.useSearch();
    const navigate = accountDetailRoute.useNavigate();
    return (
      <AccountDetailPage
        accountId={accountId}
        transactions={search}
        onTransactions={(patch) =>
          void navigate({
            search: (prev: Record<string, unknown>) =>
              parseTransactionsSearch({ ...parseTransactionsSearch(prev), ...patch }),
            replace: true,
            resetScroll: false,
          })
        }
      />
    );
  },
});

const instrumentDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/instruments/$instrumentId",
  component: function InstrumentDetailRoute() {
    const { instrumentId } = instrumentDetailRoute.useParams();
    return <InstrumentDetailPage instrumentId={instrumentId} />;
  },
});

/** `/imports/$importId?page=2`: the staged-rows page is in the URL, so refresh and links keep it. */
const importDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/imports/$importId",
  validateSearch: (search: Record<string, unknown>) => {
    const page = Number(search.page);
    return { page: Number.isInteger(page) && page > 1 ? page : undefined };
  },
  component: function ImportDetailRoute() {
    const { importId } = importDetailRoute.useParams();
    const { page } = importDetailRoute.useSearch();
    const navigate = importDetailRoute.useNavigate();
    return (
      <ImportDetailPage
        importId={importId}
        page={page ?? 1}
        onPage={(next) =>
          void navigate({ search: next > 1 ? { page: next } : {}, replace: true })
        }
      />
    );
  },
});

const incomeStreamDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/income-streams/$streamId",
  component: function IncomeStreamDetailRoute() {
    const { streamId } = incomeStreamDetailRoute.useParams();
    return <IncomeStreamDetailPage streamId={streamId} />;
  },
});

const fetchRunDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/reference-data/runs/$runId",
  component: function FetchRunDetailRoute() {
    const { runId } = fetchRunDetailRoute.useParams();
    return <FetchRunDetailPage runId={runId} />;
  },
});

/** The Guide: `/guide` is "Start here"; `/guide/tax` and the other pages are `/guide/$slug`. */
const guideRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/guide",
  component: () => <GuidePage slug={undefined} />,
});

const guidePageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/guide/$slug",
  component: function GuidePageRoute() {
    const { slug } = guidePageRoute.useParams();
    return <GuidePage slug={slug} />;
  },
});

/** `/settings/ai-log?entry=12`: the open entry is a URL search param. */
const aiLogRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings/ai-log",
  validateSearch: (search: Record<string, unknown>) => {
    const entry = Number(search.entry);
    return { entry: Number.isInteger(entry) && entry > 0 ? entry : undefined };
  },
  component: function AiLogRoute() {
    const { entry } = aiLogRoute.useSearch();
    const navigate = aiLogRoute.useNavigate();
    return (
      <AiLogPage
        entry={entry}
        onEntry={(next) => void navigate({ search: next ? { entry: next } : {}, replace: true })}
      />
    );
  },
});

/** Settings → Document profiles: `/settings/document-profiles`, `/new`, `/shipped/$key` and `/$profileId`. */
const documentProfileRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings/document-profiles/$profileId",
  component: function DocumentProfileRoute() {
    const { profileId } = documentProfileRoute.useParams();
    return <DocumentProfilePage profileId={profileId} />;
  },
});

const shippedProfileRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings/document-profiles/shipped/$key",
  component: function ShippedProfileRoute() {
    const { key } = shippedProfileRoute.useParams();
    return <ShippedProfilePage profileKey={key} />;
  },
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  recentChangesRoute,
  questionRoute,
  guideRoute,
  guidePageRoute,
  positionRoute,
  exposureRoute,
  projectionRoute,
  route("/projection/compare", ComparePage),
  route("/projection/runs", SavedRunsPage),
  savedRunDetailRoute,
  taxRoute,
  incomeReconciliationRoute,
  taxRecordsRoute,
  taxRecordDetailRoute,
  taxWhatIfsRoute,
  taxWhatIfRoute,
  taxEstimatesRoute,
  taxCompareRoute,
  taxLodgedRoute,
  taxEstimateRoute,
  libraryRoute,
  libraryDocumentRoute,
  accountsRoute,
  accountDetailRoute,
  superStatementRoute,
  route("/income-streams", IncomeStreamsPage),
  incomeStreamDetailRoute,
  route("/instruments", InstrumentsPage),
  instrumentDetailRoute,
  route("/imports", ImportsPage),
  importDetailRoute,
  route("/household", HouseholdPage),
  route("/fx-rates", FxRatesPage),
  route("/reference-data", ReferenceDataPage),
  ruleFiguresRoute,
  fetchRunDetailRoute,
  route("/categories", CategoriesPage),
  auditRoute,
  route("/settings", SettingsPage),
  aiLogRoute,
  route("/settings/document-profiles", DocumentProfilesPage),
  route("/settings/document-profiles/new", PasteProfilePage),
  shippedProfileRoute,
  documentProfileRoute,
]);

/** Browser history by default; tests pass a memory history. */
export function createAppRouter(history?: RouterHistory) {
  return createRouter({ routeTree, history, defaultPreload: false });
}

export type AppRouter = ReturnType<typeof createAppRouter>;
