// Step one of a load: which data. Each dataset the server takes from a file is
// a card that is a radio button: its name, what it is, the sheet a file must
// hold. The chosen one offers its template, the empty workbook with exactly
// the columns expected, so a file can be made right from the start.
import { Badge, Button, Group, Radio, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { Api } from "../api/client";
import { type Dataset, downloadTemplate } from "../api/datasets";
import { saveFile } from "../components/saveFile";
import classes from "../styles/app.module.css";
import { formatWhen } from "./dates";
import { named } from "./texts";

interface DatasetPickerProps {
  api: Api;
  datasets: Dataset[];
  chosen: Dataset | null;
  onChoose: (dataset: Dataset) => void;
  /** No other dataset may be chosen while a file is on its way. */
  disabled: boolean;
}

export function DatasetPicker({ api, datasets, chosen, onChoose, disabled }: DatasetPickerProps) {
  const { t } = useTranslation();
  const [downloading, setDownloading] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState(false);

  async function download(dataset: Dataset) {
    setDownloading(true);
    setDownloadFailed(false);
    try {
      const file = await downloadTemplate(api, dataset.key);
      saveFile(file.blob, file.filename ?? `${dataset.key}.xlsx`);
    } catch {
      setDownloadFailed(true);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Stack gap="sm">
      <Radio.Group
        value={chosen?.key ?? null}
        onChange={(key) => {
          const dataset = datasets.find((candidate) => candidate.key === key);
          if (dataset) {
            setDownloadFailed(false);
            onChoose(dataset);
          }
        }}
        label={t("loads.dataset.heading")}
        labelProps={{ mb: "xs" }}
      >
        <Stack gap="xs">
          {datasets.map((dataset) => (
            <Radio.Card
              key={dataset.key}
              value={dataset.key}
              className={classes.choice}
              disabled={disabled}
            >
              <Group wrap="nowrap" align="flex-start" gap="sm">
                <Radio.Indicator mt={2} />
                <Stack gap={2}>
                  <Group gap="xs">
                    <Text fw={600}>{named(t, dataset.label, dataset.key)}</Text>
                    <Badge variant="outline" color="gray" size="sm" tt="none">
                      {t(`datasets.kind.${dataset.kind}`)}
                    </Badge>
                  </Group>
                  <Text size="sm" className={classes.secondary}>
                    {named(t, dataset.description, "")}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {t("loads.dataset.sheet", {
                      sheet: dataset.sheet,
                      count: dataset.columns.length,
                    })}
                  </Text>
                  {dataset.last_load?.finished_at && (
                    <Text size="xs" c="dimmed">
                      {t("loads.dataset.lastLoad", {
                        when: formatWhen(dataset.last_load.finished_at),
                      })}
                    </Text>
                  )}
                </Stack>
              </Group>
            </Radio.Card>
          ))}
        </Stack>
      </Radio.Group>

      {chosen && (
        <Group gap="sm" wrap="wrap">
          <Button
            variant="default"
            size="compact-sm"
            loading={downloading}
            onClick={() => void download(chosen)}
          >
            {t("loads.dataset.template")}
          </Button>
          <Text size="xs" c="dimmed">
            {downloadFailed ? t("loads.dataset.templateFailed") : t("loads.dataset.templateHint")}
          </Text>
        </Group>
      )}
    </Stack>
  );
}
