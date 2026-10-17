// The dashboard section: the published Power BI report, shown with the
// signed-in user's own token for Power BI (increment 3 of
// docs/frontend-plan.md). The report's data comes from the Power BI Service
// and never passes through the API.
//
// Before the report can be drawn the page fetches three things together: a
// token for Power BI, the address Power BI embeds the report from, and the
// embedding library. While the report is on screen the page keeps its token
// fresh. Whatever goes wrong has a message of its own, shown inside the
// section so that the rest of the app stays in reach.
import { Anchor, Box, Button, Center, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";

import type { PowerBiConfig } from "../api/config";
import type { AuthClient, ServiceToken } from "../auth/authClient";
import { useSession } from "../auth/Session";
import { Panel, type PanelTone } from "../components/Panel";
import type { EmbeddedReport, EmbedReport, LoadEmbedReport } from "../powerbi/reportEmbed";
import { lookUpReport } from "../powerbi/reportLookup";
import {
  problemFromFrame,
  problemFromTokenFailure,
  ReportProblem,
  type ReportProblemKind,
} from "../powerbi/reportProblem";
import { keepTokenFresh, type TokenRenewal } from "../powerbi/tokenRenewal";
import classes from "../styles/app.module.css";

// How long the frame may take to say that the report has loaded.
export const LOAD_TIMEOUT_MS = 60_000;

// Power BI's own page is white while the report loads, so the frame waits out
// of sight until the report has loaded. If Power BI reports nothing, the frame
// shows after this long all the same, so a notice Power BI draws in it (a
// missing licence, for instance) is still seen.
export const REVEAL_AFTER_MS = 10_000;

// Power BI's name, in a frame's error, for a token that ran out.
const TOKEN_EXPIRED = "TokenExpired";

// The section's height: the window less the shell's header, footer and padding.
const SECTION_HEIGHT =
  "calc(100dvh - var(--app-shell-header-offset, 0rem) - var(--app-shell-footer-offset, 0rem)" +
  " - 2 * var(--app-shell-padding, 0rem))";
const REPORT_MIN_HEIGHT = 320;

interface DashboardPageProps {
  /** Fetches the embedding library: Microsoft's in the browser, a stand-in in tests. */
  loadEmbedReport: LoadEmbedReport;
}

export function DashboardPage({ loadEmbedReport }: DashboardPageProps) {
  const { t } = useTranslation();
  const { powerBi } = useSession();

  return (
    <Stack gap="sm" h={SECTION_HEIGHT} mih={REPORT_MIN_HEIGHT + 60}>
      <Group justify="space-between" align="baseline" wrap="nowrap">
        <Title order={2}>{t("dashboard.title")}</Title>
        {powerBi && (
          <Anchor href={powerBi.report_web_url} target="_blank" rel="noopener noreferrer" size="sm">
            {t("dashboard.openInPowerBi")}
          </Anchor>
        )}
      </Group>
      {powerBi ? (
        <Report config={powerBi} loadEmbedReport={loadEmbedReport} />
      ) : (
        <Message kind="notConfigured" />
      )}
    </Stack>
  );
}

/** What the frame needs before it can be drawn. */
interface Prepared {
  embedReport: EmbedReport;
  token: ServiceToken;
  embedUrl: string;
}

interface ReportProps extends DashboardPageProps {
  config: PowerBiConfig;
}

function Report({ config, loadEmbedReport }: ReportProps) {
  const { t } = useTranslation();
  const { auth } = useSession();
  // A problem met once the report was being drawn: a frame's error, a token
  // that ran out. Problems met before that are the query's error.
  const [later, setLater] = useState<ReportProblem | null>(null);

  const prepared = useQuery<Prepared, ReportProblem>({
    queryKey: ["powerbi-report", config.report_api_url],
    // The request is not tied to the page being on screen. React's strict mode
    // mounts every page twice in development, and a request cancelled by the
    // first unmount would be made again: two tokens asked for one visit.
    queryFn: async () => {
      // The library is fetched while Microsoft and Power BI are asked.
      const [embedReport, ready] = await Promise.all([
        loadEmbedReport().catch(() => {
          throw new ReportProblem("failed", "library_not_loaded");
        }),
        auth
          .getServiceToken(config.scope)
          .catch((error: unknown) => {
            throw problemFromTokenFailure(error);
          })
          .then(async (token) => ({
            token,
            address: await lookUpReport(config.report_api_url, token.token),
          })),
      ]);
      return { embedReport, token: ready.token, embedUrl: ready.address.embedUrl };
    },
    // A refusal does not change by asking again; the message offers a retry.
    retry: false,
    // Every visit to the section asks again: an answer kept from an earlier
    // visit holds a token that may have run out. While that is under way the
    // page waits (isFetching, below) and draws nothing from the old answer.
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const retry = () => {
    setLater(null);
    void prepared.refetch();
  };

  if (prepared.isFetching && !later) {
    return <Busy text={t("dashboard.preparing")} />;
  }
  const problem = later ?? prepared.error;
  if (problem) {
    return (
      <Message
        kind={problem.kind}
        // Back from Microsoft without the consent: its answer says more than
        // the refusal that sent the user there.
        code={problem.kind === "authorize" ? (auth.signInError ?? problem.code) : problem.code}
        requestId={problem.requestId}
        webUrl={config.report_web_url}
        onRetry={retry}
        onAuthorize={() => void auth.authorizeService(config.scope)}
      />
    );
  }
  if (!prepared.data) {
    return <Busy text={t("dashboard.preparing")} />;
  }
  return (
    <ReportFrame
      prepared={prepared.data}
      config={config}
      auth={auth}
      onProblem={setLater}
      onRetry={retry}
    />
  );
}

interface ReportFrameProps {
  prepared: Prepared;
  config: PowerBiConfig;
  auth: AuthClient;
  /** The report cannot stay on screen. */
  onProblem: (problem: ReportProblem) => void;
  /** The user asks for the report again, from the start. */
  onRetry: () => void;
}

function ReportFrame({ prepared, config, auth, onProblem, onRetry }: ReportFrameProps) {
  const { t, i18n } = useTranslation();
  const container = useRef<HTMLDivElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [slow, setSlow] = useState(false);
  const [waited, setWaited] = useState(false);
  const maxLifetimeMs = useSimulatedTokenLife();
  const title = t("dashboard.reportTitle");
  const language = i18n.language;

  useEffect(() => {
    const element = container.current;
    if (!element) {
      return;
    }
    let isLoaded = false;
    let renewal: TokenRenewal | null = null;
    let report: EmbeddedReport;
    // A frame that cannot reach Power BI reports nothing at all. Nor is it
    // known to report anything when Power BI draws a notice of its own in
    // place of the report. So silence is not taken for a failure: the frame
    // is shown after REVEAL_AFTER_MS, and later a line above it offers to
    // start over.
    const tooSlow = setTimeout(() => setSlow(true), LOAD_TIMEOUT_MS);
    const reveal = setTimeout(() => setWaited(true), REVEAL_AFTER_MS);
    try {
      report = prepared.embedReport({
        container: element,
        reportId: config.report_id,
        embedUrl: prepared.embedUrl,
        token: prepared.token.token,
        language,
        title,
        freshToken: () => renewal?.tokenForFrame() ?? Promise.resolve(null),
        onLoaded: () => {
          isLoaded = true;
          clearTimeout(tooSlow);
          setLoaded(true);
        },
        onError: (error) => {
          if (error.message === TOKEN_EXPIRED) {
            void renewal?.replaceExpired();
          } else if (!isLoaded) {
            onProblem(problemFromFrame(error));
          }
          // Once the report is on screen, Power BI shows the error of a
          // visual inside the frame itself.
        },
      });
    } catch {
      clearTimeout(tooSlow);
      clearTimeout(reveal);
      onProblem(new ReportProblem("failed", "embed_refused"));
      return;
    }

    renewal = keepTokenFresh({
      expiresAt: prepared.token.expiresAt,
      renew: () => auth.getServiceToken(config.scope, { fresh: true }),
      apply: (token) => report.setToken(token),
      onExpired: (error) => onProblem(problemFromTokenFailure(error)),
      onRenewed: import.meta.env.DEV ? logRenewal : undefined,
      maxLifetimeMs,
    });

    return () => {
      clearTimeout(tooSlow);
      clearTimeout(reveal);
      renewal?.stop();
      report.remove();
      setLoaded(false);
      setSlow(false);
      setWaited(false);
    };
  }, [prepared, config, auth, onProblem, language, title, maxLifetimeMs]);

  // The line about loading sits above the frame and never over it. The frame
  // itself waits out of sight while Power BI's white loading page is up, and
  // is never hidden for longer than REVEAL_AFTER_MS: a notice Power BI draws
  // in place of the report must stay readable.
  return (
    <Stack gap="xs" flex={1} mih={REPORT_MIN_HEIGHT}>
      {!loaded && !slow && <Status text={t("dashboard.loading")} />}
      {!loaded && slow && (
        <Group gap="sm" role="status">
          <span className={classes.dot} data-tone="warning" aria-hidden="true" />
          <Text className={classes.secondary}>{t("dashboard.slow")}</Text>
          <Button variant="default" size="compact-sm" onClick={onRetry}>
            {t("problem.retry")}
          </Button>
        </Group>
      )}
      <Box pos="relative" flex={1} className={classes.reportFrame}>
        {/* Power BI's library fills this element; React draws nothing in it. */}
        <Box
          ref={container}
          pos="absolute"
          inset={0}
          className={classes.report}
          data-waiting={loaded || waited ? undefined : true}
          data-testid="report"
        />
      </Box>
    </Stack>
  );
}

/**
 * The dev server's switch for watching the token being renewed without waiting
 * an hour: /dashboard?tokenLife=60 treats every token as lasting 60 seconds.
 * It does not exist in the production build.
 */
function useSimulatedTokenLife(): number | undefined {
  const [search] = useSearchParams();
  if (!import.meta.env.DEV) {
    return undefined;
  }
  const seconds = Number(search.get("tokenLife"));
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : undefined;
}

function logRenewal(token: ServiceToken): void {
  console.info(
    `Power BI token renewed at ${new Date().toLocaleTimeString()}; ` +
      `the new one expires at ${token.expiresAt.toLocaleTimeString()}`,
  );
}

function Status({ text }: { text: string }) {
  return (
    <Group gap="sm" role="status">
      <Loader size="sm" />
      <Text c="dimmed">{text}</Text>
    </Group>
  );
}

function Busy({ text }: { text: string }) {
  return (
    <Center flex={1} mih={REPORT_MIN_HEIGHT}>
      <Status text={text} />
    </Center>
  );
}

interface MessageProps {
  kind: ReportProblemKind | "notConfigured";
  code?: string | null;
  requestId?: string | null;
  /** The report in the Power BI Service. */
  webUrl?: string;
  onRetry?: () => void;
  onAuthorize?: () => void;
}

// What asking again cannot change: the server's settings, and a report that
// is not there.
const NO_RETRY: ReadonlySet<MessageProps["kind"]> = new Set(["notConfigured", "notFound"]);

// The panel's marker: a failure, or a state someone has to act on. The title
// says the same in words.
const TONE: Record<MessageProps["kind"], PanelTone> = {
  notConfigured: "info",
  authorize: "info",
  noAccess: "info",
  token: "error",
  refused: "error",
  notFound: "error",
  unreachable: "error",
  failed: "error",
};

function Message({ kind, code, requestId, webUrl, onRetry, onAuthorize }: MessageProps) {
  const { t } = useTranslation();

  let action: ReactNode = null;
  if (kind === "authorize" && onAuthorize) {
    action = <Button onClick={onAuthorize}>{t("dashboard.problem.authorize.action")}</Button>;
  } else if (!NO_RETRY.has(kind) && onRetry) {
    action = (
      <Button variant="default" onClick={onRetry}>
        {t("problem.retry")}
      </Button>
    );
  }

  return (
    <Panel tone={TONE[kind]} maw={560}>
      <Stack gap="xs" align="flex-start">
        <Title order={3} size="h4">
          {t(`dashboard.problem.${kind}.title`)}
        </Title>
        <Text className={classes.secondary}>{t(`dashboard.problem.${kind}.text`)}</Text>
        {kind === "noAccess" && (
          <Text className={classes.secondary}>
            {t("dashboard.problem.noAccess.hint")}{" "}
            {webUrl && (
              // Inside a sentence a link is underlined: colour alone would not
              // tell it from the words around it.
              <Anchor href={webUrl} target="_blank" rel="noopener noreferrer" underline="always">
                {t("dashboard.problem.noAccess.open")}
              </Anchor>
            )}
          </Text>
        )}
        {code && (
          <Text size="sm" c="dimmed">
            {t("dashboard.problem.code", { code })}
          </Text>
        )}
        {requestId && (
          <Text size="sm" c="dimmed">
            {t("dashboard.problem.requestId", { id: requestId })}
          </Text>
        )}
        {action}
      </Stack>
    </Panel>
  );
}
