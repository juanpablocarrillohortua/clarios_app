// The chat assistant on the dashboard: who sees it, its round button and the
// panel it opens, a question and its answer, the history sent with it, one request however many presses, every
// failure said plainly with the question back in the box, the conversation
// kept for the tab, and answers shown as plain text.
import { act, configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatRequest, ChatTurn } from "../api/chat";
import es from "../i18n/es.json";
import { paths } from "../paths";
import { json, person, renderAppAt, stubApi, viewer } from "../test/render";
import { THINKING_AFTER_MS } from "./ChatLauncher";
import { STORAGE_KEY } from "./useChat";

const CHAT = "/api/chat";
const texts = es.dashboard.chat;

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

configure({ asyncUtilTimeout: 5000 });

type Answer = (init?: RequestInit) => Response | Promise<Response>;

/** Answer every question with the same text. */
const answering =
  (answer: string): Answer =>
  () =>
    json({ answer });

/** An answer that arrives only when the test says so. */
function later() {
  let release: (response: Response) => void = () => {};
  const answer: Answer = () =>
    new Promise<Response>((resolve) => {
      release = resolve;
    });
  return { answer, release: (response: Response) => release(response) };
}

function chatCalls(fetchStub: ReturnType<typeof stubApi>) {
  return fetchStub.mock.calls.filter(([input]) => String(input) === CHAT);
}

function bodyOf(fetchStub: ReturnType<typeof stubApi>, index = 0): ChatRequest {
  const call = chatCalls(fetchStub)[index];
  return JSON.parse(String((call?.[1] as RequestInit | undefined)?.body)) as ChatRequest;
}

/** Open the panel. `fakeTimers` when the test has moved the clock to its own. */
async function openChat(fakeTimers = false) {
  const user = userEvent.setup(
    fakeTimers ? { advanceTimers: vi.advanceTimersByTime.bind(vi) } : {},
  );
  await user.click(await screen.findByRole("button", { name: texts.open }));
  const box = await screen.findByRole("textbox", { name: texts.question });
  return { user, box };
}

async function ask(question: string, fakeTimers = false) {
  const { user, box } = await openChat(fakeTimers);
  await user.type(box, question);
  await user.click(screen.getByRole("button", { name: texts.send }));
  return { user, box };
}

function conversation() {
  return screen.getByRole("log", { name: texts.conversation });
}

function saved(turns: ChatTurn[], subject = viewer.subject) {
  window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ subject, turns }));
}

describe("who sees the assistant", () => {
  it("is not on the dashboard without chat:use", async () => {
    stubApi({ me: person(["viewer"], ["dashboard:view"]) });
    renderAppAt(paths.dashboard);

    await screen.findByRole("heading", { name: es.dashboard.title });
    expect(screen.queryByRole("button", { name: texts.open })).toBeNull();
  });

  it("is on the dashboard with chat:use, closed", async () => {
    stubApi({ me: viewer });
    renderAppAt(paths.dashboard);

    const control = await screen.findByRole("button", { name: texts.open });
    expect(control).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("textbox", { name: texts.question })).toBeNull();
  });

  it("is there even when the report is not configured", async () => {
    stubApi({ me: viewer, powerBi: null });
    renderAppAt(paths.dashboard);

    expect(await screen.findByRole("button", { name: texts.open })).toBeInTheDocument();
  });
});

describe("the button and its panel", () => {
  it("opens the panel, and says what it does next", async () => {
    stubApi({ me: viewer });
    renderAppAt(paths.dashboard);

    await openChat();

    const launcher = screen.getByRole("button", { name: texts.close, expanded: true });
    expect(launcher).toHaveAttribute("aria-controls");
    const panel = screen.getByRole("region", { name: texts.heading });
    expect(panel.id).toBe(launcher.getAttribute("aria-controls"));
    expect(screen.getByRole("textbox", { name: texts.question })).toHaveFocus();
  });

  it("closed, keeps the panel out of reach", async () => {
    stubApi({ me: viewer });
    renderAppAt(paths.dashboard);

    const launcher = await screen.findByRole("button", { name: texts.open });
    const panel = document.getElementById(String(launcher.getAttribute("aria-controls")));

    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveAttribute("inert");
    expect(screen.queryByRole("region", { name: texts.heading })).toBeNull();
  });

  it.each([
    ["its cross", "cross"],
    ["Escape", "escape"],
    ["the button again", "button"],
  ])("closes with %s, and focus goes back to the button", async (_, how) => {
    stubApi({ me: viewer });
    renderAppAt(paths.dashboard);
    const { user } = await openChat();
    const panel = screen.getByRole("region", { name: texts.heading });

    if (how === "cross") await user.click(within(panel).getByRole("button", { name: texts.close }));
    else if (how === "escape") await user.keyboard("{Escape}");
    else await user.click(screen.getByRole("button", { name: texts.close, expanded: true }));

    const launcher = await screen.findByRole("button", { name: texts.open });
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(launcher).toHaveFocus();
    expect(screen.queryByRole("textbox", { name: texts.question })).toBeNull();
  });

  it("marks an answer that arrived while it was closed, until it opens", async () => {
    const pending = later();
    stubApi({ me: viewer, answers: { [CHAT]: pending.answer } });
    renderAppAt(paths.dashboard);
    const { user } = await ask("¿Cuántas EQ?");

    await user.keyboard("{Escape}");
    expect(screen.queryByText(texts.unread)).toBeNull();
    await act(async () => pending.release(json({ answer: "Muchas." })));

    expect(await screen.findByText(texts.unread)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: texts.open }));
    expect(within(conversation()).getByText("Muchas.")).toBeInTheDocument();
    expect(screen.queryByText(texts.unread)).toBeNull();
  });

  it("marks nothing for an answer seen as it arrived", async () => {
    stubApi({ me: viewer, answers: { [CHAT]: answering("Ya.") } });
    renderAppAt(paths.dashboard);
    const { user } = await ask("¿Ahora?");
    await within(conversation()).findByText("Ya.");

    await user.keyboard("{Escape}");

    expect(screen.getByRole("button", { name: texts.open })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByText(texts.unread)).toBeNull();
  });
});

describe("a question", () => {
  it("is answered, and the next one carries the conversation", async () => {
    const fetchStub = stubApi({ me: viewer, answers: { [CHAT]: answering("Hubo 196.712 EQ.") } });
    renderAppAt(paths.dashboard);

    const { user, box } = await ask("¿Cuántas EQ hubo en septiembre?");

    expect(await within(conversation()).findByText("Hubo 196.712 EQ.")).toBeInTheDocument();
    expect(bodyOf(fetchStub)).toEqual({ history: [], question: "¿Cuántas EQ hubo en septiembre?" });
    expect(chatCalls(fetchStub)[0]?.[1]?.method).toBe("POST");

    await user.type(box, "¿Y en agosto?{Enter}");

    await waitFor(() => expect(chatCalls(fetchStub)).toHaveLength(2));
    expect(bodyOf(fetchStub, 1)).toEqual({
      history: [
        { role: "user", content: "¿Cuántas EQ hubo en septiembre?" },
        { role: "assistant", content: "Hubo 196.712 EQ." },
      ],
      question: "¿Y en agosto?",
    });
  });

  it("shows at once, before its answer", async () => {
    const pending = later();
    stubApi({ me: viewer, answers: { [CHAT]: pending.answer } });
    renderAppAt(paths.dashboard);

    const { box } = await ask("¿Cuántas EQ hubo?");

    expect(within(conversation()).getByText("¿Cuántas EQ hubo?")).toBeInTheDocument();
    expect(box).toHaveValue("");
    expect(box).toHaveFocus();
    expect(screen.getByRole("button", { name: texts.send })).toBeDisabled();

    await act(async () => pending.release(json({ answer: "Muchas." })));
    expect(await within(conversation()).findByText("Muchas.")).toBeInTheDocument();
    expect(within(conversation()).getAllByText("¿Cuántas EQ hubo?")).toHaveLength(1);
  });

  it("is sent once however many times it is sent", async () => {
    const pending = later();
    const fetchStub = stubApi({ me: viewer, answers: { [CHAT]: pending.answer } });
    renderAppAt(paths.dashboard);
    const { user, box } = await openChat();
    await user.type(box, "Hola");

    const send = screen.getByRole("button", { name: texts.send });
    fireEvent.click(send);
    fireEvent.click(send);
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(chatCalls(fetchStub)).toHaveLength(1));
    await act(async () => pending.release(json({ answer: "Hola." })));
    await within(conversation()).findByText("Hola.");
    expect(chatCalls(fetchStub)).toHaveLength(1);
  });

  it("is not sent when blank, and Shift+Enter starts a new line", async () => {
    const fetchStub = stubApi({ me: viewer, answers: { [CHAT]: answering("No.") } });
    renderAppAt(paths.dashboard);
    const { user, box } = await openChat();

    await user.type(box, "   {Enter}");
    expect(screen.getByRole("button", { name: texts.send })).toBeDisabled();

    await user.clear(box);
    await user.type(box, "uno{Shift>}{Enter}{/Shift}dos");

    expect(box).toHaveValue("uno\ndos");
    expect(chatCalls(fetchStub)).toHaveLength(0);
  });

  it("shows the assistant at work only once the wait lasts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const pending = later();
    stubApi({ me: viewer, answers: { [CHAT]: pending.answer } });
    renderAppAt(paths.dashboard);

    await ask("¿Algo?", true);
    expect(screen.queryByText(texts.pending)).toBeNull();

    await act(async () => vi.advanceTimersByTime(THINKING_AFTER_MS));
    expect(await screen.findByText(texts.pending)).toBeInTheDocument();

    await act(async () => pending.release(json({ answer: "Listo." })));
    await within(conversation()).findByText("Listo.");
    expect(screen.queryByText(texts.pending)).toBeNull();
  });

  it("gets its answer shown as text, never as HTML", async () => {
    stubApi({ me: viewer, answers: { [CHAT]: answering("<b>Negrita</b> & <i>más</i>") } });
    renderAppAt(paths.dashboard);

    await ask("¿HTML?");

    expect(
      await within(conversation()).findByText("<b>Negrita</b> & <i>más</i>"),
    ).toBeInTheDocument();
    expect(conversation().querySelector("b, i")).toBeNull();
  });
});

describe("a failure", () => {
  it.each([
    ["chat_not_configured", 503, texts.failure.notConfigured],
    ["chat_model_unavailable", 502, texts.failure.unavailable],
    ["unauthenticated", 401, texts.failure.unknown],
  ])("%s is said, and the question goes back in the box", async (code, status, text) => {
    stubApi({ me: viewer, answers: { [CHAT]: () => json({ code, message: "x" }, status) } });
    renderAppAt(paths.dashboard);

    const { box } = await ask("¿Cuántas EQ?");

    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(box).toHaveValue("¿Cuántas EQ?");
    expect(within(conversation()).queryByText("¿Cuántas EQ?")).toBeNull();
  });

  it("without an answer at all is said too", async () => {
    stubApi({
      me: viewer,
      answers: {
        [CHAT]: () => {
          throw new TypeError("Failed to fetch");
        },
      },
    });
    renderAppAt(paths.dashboard);

    const { box } = await ask("¿Hola?");

    expect(await screen.findByRole("alert")).toHaveTextContent(texts.failure.unknown);
    expect(box).toHaveValue("¿Hola?");
  });

  it("keeps what was typed while waiting, after the question", async () => {
    const pending = later();
    stubApi({ me: viewer, answers: { [CHAT]: pending.answer } });
    renderAppAt(paths.dashboard);

    const { user, box } = await ask("Primera");
    await user.type(box, "Segunda");
    await act(async () => pending.release(json({ code: "chat_model_unavailable" }, 502)));

    await screen.findByRole("alert");
    expect(box).toHaveValue("Primera\nSegunda");
  });
});

describe("the examples", () => {
  it("fill the box and send nothing", async () => {
    const fetchStub = stubApi({ me: viewer });
    renderAppAt(paths.dashboard);
    const { user, box } = await openChat();

    await user.click(screen.getByRole("button", { name: texts.examples.equsMonth }));

    expect(box).toHaveValue(texts.examples.equsMonth);
    expect(box).toHaveFocus();
    expect(chatCalls(fetchStub)).toHaveLength(0);
  });
});

describe("the conversation", () => {
  it("is kept for the tab and restored", async () => {
    saved([
      { role: "user", content: "¿Antes?" },
      { role: "assistant", content: "Sí, antes." },
    ]);
    stubApi({ me: viewer });
    renderAppAt(paths.dashboard);

    await openChat();

    expect(within(conversation()).getByText("Sí, antes.")).toBeInTheDocument();
  });

  it("is not shown to someone else signing in on the same tab", async () => {
    saved([{ role: "assistant", content: "De otra persona." }], "someone-else");
    stubApi({ me: viewer });
    renderAppAt(paths.dashboard);

    await openChat();

    expect(screen.queryByText("De otra persona.")).toBeNull();
  });

  it("is sent trimmed to the API's limit", async () => {
    const long: ChatTurn[] = Array.from({ length: 210 }, (_, index) => ({
      role: index % 2 === 0 ? "user" : "assistant",
      content: `mensaje ${index}`,
    }));
    saved(long);
    const fetchStub = stubApi({ me: viewer, answers: { [CHAT]: answering("Bien.") } });
    renderAppAt(paths.dashboard);

    await ask("¿Y ahora?");

    await waitFor(() => expect(chatCalls(fetchStub)).toHaveLength(1));
    const { history } = bodyOf(fetchStub);
    expect(history).toHaveLength(200);
    expect(history.at(-1)?.content).toBe("mensaje 209");
  });

  it("starts again with Nueva conversación", async () => {
    stubApi({ me: viewer, answers: { [CHAT]: answering("Una respuesta.") } });
    renderAppAt(paths.dashboard);
    const { user } = await ask("Una pregunta");
    await within(conversation()).findByText("Una respuesta.");

    await user.click(screen.getByRole("button", { name: texts.newConversation }));

    expect(screen.queryByText("Una respuesta.")).toBeNull();
    expect(screen.getByRole("button", { name: texts.examples.equsMonth })).toBeInTheDocument();
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
