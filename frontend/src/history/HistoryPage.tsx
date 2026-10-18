// "Historial": every load that landed in the database, the app's and the
// command line's, newest first, with the person, the dataset and the state
// as filters, and the dates under "Más filtros". The filters live in the
// address, so the back button, a reload and a link shared with a colleague
// all keep them.
//
// On a wide screen it is a table; on a phone, each load is a card, in the
// same order. Each opens the load's detail. While the next page arrives the
// current one stays, dimmed: nothing blinks.
import {
  Anchor,
  Box,
  Button,
  Group,
  Select,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { useReducedMotion } from "@mantine/hooks";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import {
  fetchLoads,
  type LoadOrigin,
  type LoadQuery,
  type LoadState,
  type Person,
} from "../api/loads";
import { useSession } from "../auth/Session";
import { Panel } from "../components/Panel";
import { useWideLayout } from "../layout/useWideLayout";
import { formatWhen } from "../loads/dates";
import { formatCount } from "../loads/formatBytes";
import { LoadsHeading, useDatasets } from "../loads/LoadsPage";
import { LoadsTabs } from "../loads/LoadsTabs";
import { named } from "../loads/texts";
import { loadPath, paths } from "../paths";
import classes from "../styles/app.module.css";
import { loaderName, loadName } from "./loadNames";

const PAGE = 25;
const STATES: LoadState[] = ["completed", "failed", "running", "pending", "cancelled"];
const ORIGINS: LoadOrigin[] = ["app", "cli"];
/** The filters, by their name in the address. */
const FILTERS = [
  "dataset",
  "state",
  "user",
  "origin",
  "periodFrom",
  "periodTo",
  "from",
  "to",
] as const;
type Filter = (typeof FILTERS)[number];

function queryOf(search: URLSearchParams): LoadQuery {
  const value = (name: Filter) => search.get(name) || undefined;
  return {
    dataset: value("dataset"),
    state: value("state") as LoadState | undefined,
    user: value("user"),
    origin: value("origin") as LoadOrigin | undefined,
    periodFrom: value("periodFrom"),
    periodTo: value("periodTo"),
    from: value("from"),
    to: value("to"),
    offset: Math.max(0, Number(search.get("offset") ?? 0) || 0),
    limit: PAGE,
  };
}

/** A state as a dot and a word: never colour alone. */
function StateMark({ state }: { state: LoadState }) {
  const { t } = useTranslation();
  const tone = state === "completed" ? "online" : state === "failed" ? "error" : "info";
  return (
    <Group gap={6} wrap="nowrap">
      <span className={classes.dot} data-tone={tone} aria-hidden="true" />
      <span>{t(`history.states.${state}`)}</span>
    </Group>
  );
}

function people(t: TFunction, found: Person[]) {
  return [
    { value: "me", label: t("history.filters.mine") },
    ...found.map((person) => ({ value: person.subject, label: person.name ?? person.subject })),
  ];
}

export function HistoryPage() {
  const { t } = useTranslation();
  const { api, me } = useSession();
  const wide = useWideLayout();
  const reduced = useReducedMotion();
  const datasets = useDatasets();
  const [search, setSearch] = useSearchParams();
  const query = queryOf(search);

  const loads = useQuery({
    queryKey: ["loads", query],
    queryFn: ({ signal }) => fetchLoads(api, query, signal),
    placeholderData: keepPreviousData,
    // One more try, then say so: the person is waiting to read.
    retry: 1,
  });

  function set(name: Filter, value: string | null) {
    const next = new URLSearchParams(search);
    if (value) next.set(name, value);
    else next.delete(name);
    next.delete("offset");
    setSearch(next, { replace: true });
  }
  function page(offset: number) {
    const next = new URLSearchParams(search);
    if (offset > 0) next.set("offset", String(offset));
    else next.delete("offset");
    setSearch(next);
  }
  const filtered = FILTERS.some((name) => search.get(name));
  const dated = (["periodFrom", "periodTo", "from", "to"] as const).some((name) =>
    search.get(name),
  );
  const data = loads.data;
  const items = data?.items ?? [];
  // Someone else's subject is offered only as a name; "me" is the caller.
  const others = (data?.people ?? []).filter((person) => person.subject !== me.subject);

  return (
    <Box maw={1100}>
      <Stack gap="lg">
        <Stack gap={4}>
          <LoadsHeading />
          <LoadsTabs />
          <Text className={classes.secondary} mt="xs">
            {t("history.intro")}
          </Text>
        </Stack>

        <Stack gap="xs">
          <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} spacing="sm">
            <Select
              label={t("history.filters.dataset")}
              placeholder={t("history.filters.anyDataset")}
              clearable
              data={(datasets.data?.datasets ?? []).map((dataset) => ({
                value: dataset.key,
                label: named(t, dataset.label, dataset.key),
              }))}
              value={query.dataset ?? null}
              onChange={(value) => set("dataset", value)}
            />
            <Select
              label={t("history.filters.person")}
              placeholder={t("history.filters.anyPerson")}
              clearable
              data={people(t, others)}
              value={query.user ?? null}
              onChange={(value) => set("user", value)}
            />
            <Select
              label={t("history.filters.state")}
              placeholder={t("history.filters.anyState")}
              clearable
              data={STATES.map((state) => ({ value: state, label: t(`history.states.${state}`) }))}
              value={query.state ?? null}
              onChange={(value) => set("state", value)}
            />
            <Select
              label={t("history.filters.origin")}
              placeholder={t("history.filters.anyOrigin")}
              clearable
              data={ORIGINS.map((origin) => ({
                value: origin,
                label: t(`history.origins.${origin}`),
              }))}
              value={query.origin ?? null}
              onChange={(value) => set("origin", value)}
            />
          </SimpleGrid>
          <details className={classes.disclosure} open={dated || undefined}>
            <summary>{t("history.filters.more")}</summary>
            <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} spacing="sm" mt="xs">
              {(["periodFrom", "periodTo", "from", "to"] as const).map((name) => (
                <TextInput
                  key={name}
                  type="date"
                  label={t(`history.filters.${name}`)}
                  value={search.get(name) ?? ""}
                  onChange={(event) => set(name, event.currentTarget.value || null)}
                />
              ))}
            </SimpleGrid>
          </details>
        </Stack>

        {loads.isPending && (
          <Stack gap="xs" aria-busy="true" aria-label={t("history.loading")}>
            <Skeleton height={44} radius="sm" animate={!reduced} />
            <Skeleton height={44} radius="sm" animate={!reduced} />
            <Skeleton height={44} radius="sm" animate={!reduced} />
          </Stack>
        )}

        {loads.isError && !data && (
          <Panel tone="error">
            <Stack gap="sm" align="flex-start">
              <Text>{t("history.failed")}</Text>
              <Button variant="default" size="compact-sm" onClick={() => void loads.refetch()}>
                {t("problem.retry")}
              </Button>
            </Stack>
          </Panel>
        )}

        {data && items.length === 0 && (
          <Panel>
            <Stack gap="sm" align="flex-start">
              <Text>{filtered ? t("history.noMatch") : t("history.empty")}</Text>
              {filtered ? (
                <Button
                  variant="default"
                  size="compact-sm"
                  onClick={() => setSearch(new URLSearchParams(), { replace: true })}
                >
                  {t("history.clearFilters")}
                </Button>
              ) : (
                <Anchor component={Link} to={paths.loads} size="sm">
                  {t("history.emptyAction")}
                </Anchor>
              )}
            </Stack>
          </Panel>
        )}

        {items.length > 0 && (
          <Stack gap="sm" className={loads.isPlaceholderData ? classes.stale : undefined}>
            {wide ? (
              <Table className={classes.issueTable} highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t("history.columns.when")}</Table.Th>
                    <Table.Th>{t("history.columns.dataset")}</Table.Th>
                    <Table.Th>{t("history.columns.file")}</Table.Th>
                    <Table.Th ta="right">{t("history.columns.rows")}</Table.Th>
                    <Table.Th ta="right">{t("history.columns.warnings")}</Table.Th>
                    <Table.Th>{t("history.columns.state")}</Table.Th>
                    <Table.Th>{t("history.columns.person")}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {items.map((load) => (
                    <Table.Tr key={load.run_id}>
                      <Table.Td className={classes.number}>
                        <Anchor component={Link} to={loadPath(load.run_id)} size="sm">
                          {formatWhen(load.finished_at ?? load.started_at)}
                        </Anchor>
                      </Table.Td>
                      <Table.Td>{loadName(t, datasets.data?.datasets, load)}</Table.Td>
                      <Table.Td style={{ overflowWrap: "anywhere" }}>
                        {load.filename ?? t("history.noFile")}
                      </Table.Td>
                      <Table.Td ta="right" className={classes.number}>
                        {formatCount(load.rows)}
                      </Table.Td>
                      <Table.Td ta="right" className={classes.number}>
                        {formatCount(load.warnings_accepted)}
                      </Table.Td>
                      <Table.Td>
                        <StateMark state={load.state} />
                      </Table.Td>
                      <Table.Td>{loaderName(t, load)}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            ) : (
              <Stack component="ul" gap={0} p={0} m={0} aria-label={t("loads.tabs.history")}>
                {items.map((load) => (
                  <li key={load.run_id} className={classes.issueCard}>
                    <Stack gap={2}>
                      <Group justify="space-between" wrap="nowrap" gap="sm">
                        <Anchor component={Link} to={loadPath(load.run_id)} fw={600}>
                          {loadName(t, datasets.data?.datasets, load)}
                        </Anchor>
                        <StateMark state={load.state} />
                      </Group>
                      <Text size="sm" style={{ overflowWrap: "anywhere" }}>
                        {load.filename ?? t("history.noFile")}
                      </Text>
                      <Text size="xs" c="dimmed">
                        {formatWhen(load.finished_at ?? load.started_at)} · {loaderName(t, load)} ·{" "}
                        {formatCount(load.rows)} {t("history.columns.rows").toLowerCase()}
                      </Text>
                    </Stack>
                  </li>
                ))}
              </Stack>
            )}
            <Group justify="space-between">
              <Text size="sm" c="dimmed">
                {t("history.range", {
                  from: formatCount(query.offset + 1),
                  to: formatCount(query.offset + items.length),
                  total: formatCount(data?.total ?? 0),
                })}
              </Text>
              {(data?.total ?? 0) > PAGE && (
                <Group gap="xs">
                  <Button
                    variant="default"
                    size="compact-sm"
                    disabled={query.offset === 0}
                    onClick={() => page(Math.max(0, query.offset - PAGE))}
                  >
                    {t("history.previous")}
                  </Button>
                  <Button
                    variant="default"
                    size="compact-sm"
                    disabled={query.offset + PAGE >= (data?.total ?? 0)}
                    onClick={() => page(query.offset + PAGE)}
                  >
                    {t("history.next")}
                  </Button>
                </Group>
              )}
            </Group>
          </Stack>
        )}
      </Stack>
    </Box>
  );
}
