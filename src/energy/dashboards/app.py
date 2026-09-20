"""Plotly Dash application factory.

Layer: dashboards

Responsibility:
    Builds the Dash application: registers the layout, attaches callbacks, and
    configures the URL prefix it is served under behind Caddy. One of three
    sibling entry points, so it must never import ``api`` or ``pipelines``. It
    reads Gold through services and renders it, performing no aggregation of its
    own - a figure on a chart must be the same number the API returns for the
    same question.

    The factory itself is implemented in Phase 1 because it is infrastructure
    wiring, exactly like :mod:`energy.api.main`, and because the ``dash``
    container must reach a healthy state for ``docker compose up`` to succeed.
    Until Phase 5 supplies the real layouts and callbacks it serves a holding
    page; :mod:`energy.dashboards.layouts`,
    :mod:`energy.dashboards.callbacks` and
    :mod:`energy.dashboards.components.charts` remain placeholders.

Depends on:
    ``energy.dashboards.layouts``, ``energy.dashboards.callbacks``,
    ``energy.services``, ``energy.core``, ``dash``.

Implemented in:
    Phase 5 - Analytics, alerting and reporting (layouts and callbacks)
"""

from __future__ import annotations

from dash import Dash, html

from energy.core.logging import configure_from_settings, get_logger
from energy.core.settings import Settings, get_settings

logger = get_logger(__name__)

PLACEHOLDER_TITLE = "Clarios Energy - Dashboards"


def _holding_page(settings: Settings) -> html.Div:
    """Build the Phase 1 holding page.

    Replaced by :func:`energy.dashboards.layouts.main_layout` in Phase 5. It
    exists so the service starts, answers its healthcheck, and states plainly
    what is and is not built yet.

    Args:
        settings: process configuration, displayed for operator convenience.

    Returns:
        A Dash component tree.
    """
    return html.Div(
        style={
            "fontFamily": "system-ui, -apple-system, sans-serif",
            "maxWidth": "48rem",
            "margin": "4rem auto",
            "padding": "0 1.5rem",
            "lineHeight": "1.6",
        },
        children=[
            html.H1("Clarios Energy"),
            html.P("Industrial energy and gas consumption monitoring."),
            html.Hr(),
            html.H2("Phase 1 - Foundations"),
            html.P(
                "The dashboard service is running. Layouts, callbacks and charts "
                "are delivered in Phase 5; until then this page confirms the "
                "container is healthy and correctly wired."
            ),
            html.Ul(
                [
                    html.Li(f"Environment: {settings.app_env}"),
                    html.Li(f"Version: {settings.app_version}"),
                    html.Li(html.A("API documentation", href="/api/docs")),
                    html.Li(html.A("API health", href="/api/health")),
                ]
            ),
        ],
    )


def create_dash_app(settings: Settings | None = None) -> Dash:
    """Build and configure the Dash application.

    Args:
        settings: configuration to use; defaults to the cached process settings.

    Returns:
        A configured :class:`~dash.Dash` instance with its layout set. From
        Phase 5 onward this also registers every callback via
        :func:`energy.dashboards.callbacks.register_callbacks`.
    """
    settings = settings or get_settings()
    configure_from_settings()

    app = Dash(
        __name__,
        title=PLACEHOLDER_TITLE,
        # Empty string disables the "Updating..." title flicker on callbacks.
        update_title="",
        url_base_pathname=settings.dash.url_base_pathname,
        suppress_callback_exceptions=True,
    )
    app.layout = _holding_page(settings)

    logger.info(
        "dash application created",
        base_pathname=settings.dash.url_base_pathname,
        environment=settings.app_env,
    )
    return app


def serve(settings: Settings | None = None) -> None:
    """Run the Dash server.

    Args:
        settings: configuration to use; defaults to the cached process settings.
    """
    settings = settings or get_settings()
    app = create_dash_app(settings)
    app.run(
        host=settings.dash.host,
        port=settings.dash.port,
        debug=settings.app_debug and not settings.is_production,
    )


if __name__ == "__main__":
    serve()


__all__ = ["PLACEHOLDER_TITLE", "create_dash_app", "serve"]
