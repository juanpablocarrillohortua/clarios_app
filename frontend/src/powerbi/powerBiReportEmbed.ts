// Shows a Power BI report through Microsoft's embedding library. This is the
// only file that imports the library; everything else sees the EmbedReport
// interface.
//
// The model is "embed for your organization": the report opens with the
// signed-in user's own Entra ID token, and Power BI decides what that user may
// see. The app only views: it asks for no permission to edit or save.
import { factories, type models as Models, models, service } from "powerbi-client";

import type { EmbedReport } from "./reportEmbed";

// One service for the page. It owns the channel to every Power BI frame.
const powerBi = new service.Service(
  factories.hpmFactory,
  factories.wpmpFactory,
  factories.routerFactory,
);

export const embedPowerBiReport: EmbedReport = (options) => {
  const report = powerBi.embed(options.container, {
    type: "report",
    id: options.reportId,
    embedUrl: options.embedUrl,
    accessToken: options.token,
    // The user's own token, not an embed token made by a server.
    tokenType: models.TokenType.Aad,
    permissions: models.Permissions.Read,
    viewMode: models.ViewMode.View,
    eventHooks: { accessTokenProvider: options.freshToken },
    settings: {
      // The margins around the report page take the app's own colour.
      background: models.BackgroundType.Transparent,
      localeSettings: { language: options.language, formatLocale: options.language },
      panes: {
        // What the pane holds is the report author's decision, so it stays
        // available; collapsed, it costs a narrow strip.
        filters: { visible: true, expanded: false },
        // A report with several pages cannot be used without it.
        pageNavigation: { visible: true, position: models.PageNavigationPosition.Bottom },
      },
    },
  });

  // The library gives its frame the container's size and nothing else.
  report.iframe.style.border = "0";
  report.setComponentTitle(options.title);

  report.on("loaded", options.onLoaded);
  // Power BI may send the same error more than once: a text that is no token
  // was refused twice in the same instant (seen on 2026-10-13).
  report.on<Models.IError>("error", (event) => {
    // The library raises this as a DOM event named "error" that bubbles. At
    // the window it would pass for an uncaught error with whatever listens
    // there: the dev server's client reported it as one (2026-10-13).
    event.stopPropagation();
    const { message, errorCode, detailedMessage, technicalDetails } = event.detail;
    options.onError({
      message,
      detail: [errorCode, detailedMessage].filter(Boolean).join(" · ") || null,
      requestId: technicalDetails?.requestId ?? null,
    });
  });

  return {
    setToken: (token) => report.setAccessToken(token),
    remove() {
      report.off("loaded");
      report.off("error");
      powerBi.reset(options.container);
    },
  };
};
