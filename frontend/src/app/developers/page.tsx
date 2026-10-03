import { absoluteUrl, pageMetadata } from "@/lib/site";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import CopyText from "@/components/CopyText";
import { Summary, Point, Section, P, List, Item, More, A } from "@/components/about/AboutPage";
import {
  codeSpans,
  endpointsByTag,
  nestedObject,
  paramTypeLabel,
  responseSchema,
  typeLabel,
  type Endpoint,
  type Parameter,
  type Schema,
  type Spec,
} from "@/lib/openapi";

export const metadata = pageMetadata({
  title: "Public API and MCP server",
  description:
    "Civitas's scores, member records and document search as an open JSON API and an MCP server for AI assistants. No key, no account.",
  path: "/developers",
});

// Rendered per request (the spec fetch below is cached for an hour): `next
// build` runs where the backend isn't reachable, and the page must describe
// the API that is actually deployed.
export const dynamic = "force-dynamic";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const API = "/api/public/v1";
const MCP_URL = absoluteUrl(`${API}/mcp`);
const SPEC_URL = absoluteUrl(`${API}/openapi.json`);

/** The spec is the backend's, generated from the routes: never a copy. */
async function fetchSpec(): Promise<Spec | null> {
  try {
    const res = await fetch(`${BACKEND}${API}/openapi.json`, { next: { revalidate: 3600 } });
    if (!res.ok) return null;
    const data = await res.json();
    return data?.paths && data?.info ? (data as Spec) : null;
  } catch {
    return null;
  }
}

const CODE = "font-mono text-xs text-ink-hi";

/** Docstring prose, with its `code` spans set as code. */
function Prose({ text }: { text: string }) {
  return (
    <>
      {codeSpans(text).map((part, i) =>
        part.code ? (
          <code key={i} className={CODE}>
            {part.text}
          </code>
        ) : (
          part.text
        )
      )}
    </>
  );
}

function Params({ params }: { params: Parameter[] }) {
  return (
    <dl className="divide-y divide-white/[0.05] border-y border-white/[0.07]">
      {params.map((p) => (
        <div key={p.name} className="grid gap-1 py-2 sm:grid-cols-[11rem_1fr] sm:gap-4">
          <dt>
            <code className={CODE}>{p.name}</code>
            <span className="ml-2 font-mono text-[11px] text-ink-min">
              {p.in === "path" ? "in path" : p.required ? "required" : "optional"}
            </span>
          </dt>
          <dd className="space-y-0.5 text-sm text-ink-mid">
            <p className="font-mono text-xs text-ink-lo">
              {paramTypeLabel(p.schema)}
              {p.schema.default !== undefined &&
                p.schema.default !== null &&
                ` · default ${String(p.schema.default)}`}
            </p>
            {p.description && (
              <p>
                <Prose text={p.description} />
              </p>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** A response body's fields, one level at a time: nested objects open on request. */
function Fields({
  schema,
  spec,
  depth = 0,
  context,
}: {
  schema: Schema;
  spec: Spec;
  depth?: number;
  /** Which endpoint these are fields of, for the nested disclosures' names (More). */
  context?: string;
}) {
  const properties = Object.entries(schema.properties ?? {});
  return (
    <dl className="divide-y divide-white/[0.05] border-y border-white/[0.07]">
      {properties.map(([name, field]) => {
        const nested = depth < 2 ? nestedObject(field, spec) : null;
        return (
          <div key={name} className="grid gap-1 py-2 sm:grid-cols-[11rem_1fr] sm:gap-4">
            <dt>
              <code className={`${CODE} break-all`}>{name}</code>
            </dt>
            <dd className="min-w-0 space-y-1 text-sm text-ink-mid">
              <p className="font-mono text-xs text-ink-lo">{typeLabel(field)}</p>
              {field.description && (
                <p>
                  <Prose text={field.description} />
                </p>
              )}
              {nested && (
                <More label={`Fields of ${name}`} context={context}>
                  <Fields schema={nested} spec={spec} depth={depth + 1} context={context} />
                </More>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function EndpointBlock({ endpoint, spec }: { endpoint: Endpoint; spec: Spec }) {
  const { method, path, op } = endpoint;
  const params = op.parameters ?? [];
  const body = responseSchema(op, spec);
  const listOf = body?.type === "array" && body.items ? nestedObject(body, spec) : null;
  // Paths with nothing to fill in can be opened as they are.
  const openable = !path.includes("{");
  return (
    <article id={op.operationId} className="scroll-mt-24 space-y-3">
      <h4 className="font-display text-lg font-semibold text-ink-hi">{op.summary}</h4>
      <div className="space-y-0.5 font-mono">
        <p className="break-all text-sm">
          <span className="text-signal-cyan">{method}</span>{" "}
          {openable ? (
            <a
              href={path}
              className="text-ink-hi underline decoration-ink-min/50 underline-offset-2 hover:text-phos"
            >
              {path}
            </a>
          ) : (
            <span className="text-ink-hi">{path}</span>
          )}
        </p>
        <p className="text-xs text-ink-min">MCP tool: {op.operationId}</p>
      </div>
      {op.description && (
        <div className="space-y-2 text-sm text-ink-mid">
          {op.description.split("\n\n").map((para) => (
            <p key={para}>
              <Prose text={para.replace(/\s*\n\s*/g, " ")} />
            </p>
          ))}
        </div>
      )}
      {params.length > 0 && (
        <div className="space-y-1">
          <h5 className="font-mono text-xs uppercase tracking-wider text-ink-lo">Parameters</h5>
          <Params params={params} />
        </div>
      )}
      {body && (
        <More
          label={listOf ? "Response: a list; each item has these fields" : "Response fields"}
          context={`of ${method} ${path}`}
        >
          <Fields schema={listOf ?? body} spec={spec} context={`of ${method} ${path}`} />
        </More>
      )}
    </article>
  );
}

/* Every claim here is checked against the code (backend/app/api/public.py,
   public_mcp.py, rate_limit.py). The endpoint reference below is the live
   spec, so only the prose around it can go stale. Change it with them. */
export default async function DevelopersPage() {
  const spec = await fetchSpec();
  const groups = spec ? endpointsByTag(spec) : [];
  const perMinute = spec?.info["x-rate-limit-per-minute"];

  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-3xl mx-auto font-sans">
          <PageMasthead
            className="mb-6"
            eyebrow="Developers · open data"
            title="Public API and MCP server"
          >
            <p>
              The scores, member records and document search behind this site, as JSON for your own
              program and as tools for an AI assistant.
            </p>
          </PageMasthead>

          <div className="mt-10 space-y-12">
            <Summary>
              <Point>
                No key, no account and nothing to sign up for. Open to every origin, so a page in a
                browser can call it directly.
              </Point>
              <Point>
                {perMinute
                  ? `${perMinute} requests a minute`
                  : "A fixed number of requests a minute"}{" "}
                from each address, counted across the API and the MCP server together. Every API
                answer says how many are left.
              </Point>
              <Point>
                The reference below is generated from the running code, so it always describes what
                the API actually returns.
              </Point>
            </Summary>

            <Section id="start" title="Getting started">
              <P>
                Every endpoint is a GET that returns JSON. Serving senators from Georgia, ranked by
                score:
              </P>
              <CopyText
                text={`curl "${absoluteUrl(`${API}/senators?state=GA`)}"`}
                label="example request"
              />
              <List>
                <Item label="Ids">
                  A member&apos;s id is the one in their Civitas address:{" "}
                  <code className={CODE}>jon-ossoff</code> in{" "}
                  <code className={CODE}>/politicians/jon-ossoff</code>. The lists give each one,
                  and every record carries <code className={CODE}>siteUrl</code>, its page here, to
                  link or cite.
                </Item>
                <Item label="Lists">
                  Pages of <code className={CODE}>entries</code> with{" "}
                  <code className={CODE}>total</code>, <code className={CODE}>page</code>,{" "}
                  <code className={CODE}>perPage</code> and <code className={CODE}>totalPages</code>
                  . A member&apos;s <code className={CODE}>rank</code> is their place in the whole
                  chamber, whatever you filter by.
                </Item>
                <Item label="Scores">
                  0 to 100, higher is a better representative.{" "}
                  <code className={CODE}>representationScore.overall</code> is the weighted score;
                  the weights are in the{" "}
                  <a
                    href={API}
                    className="text-signal-cyan underline underline-offset-2 hover:text-phos"
                  >
                    index
                  </a>
                  . How each one is computed:{" "}
                  <A href="/about/scores">how senators and representatives are scored</A>.
                </Item>
                <Item label="Errors">
                  An unknown id is 404, a parameter out of range or not among the listed values is
                  422 with the reason in <code className={CODE}>detail</code>, and past the rate
                  limit it is 429 with <code className={CODE}>Retry-After</code>.
                </Item>
              </List>
              <More label="Rate-limit headers, caching and the spec">
                <p>
                  Each answer carries <code className={CODE}>X-RateLimit-Limit</code>,{" "}
                  <code className={CODE}>X-RateLimit-Remaining</code> and{" "}
                  <code className={CODE}>X-RateLimit-Reset</code> (a Unix time).{" "}
                  <code className={CODE}>Cache-Control</code> says how long an answer can be reused.
                </p>
                <p>The OpenAPI 3 description, to generate a client from:</p>
                <CopyText text={SPEC_URL} label="OpenAPI spec address" />
              </More>
            </Section>

            <Section id="mcp" title="Use it from an AI assistant (MCP)">
              <P>
                The same API is an MCP server, so an assistant such as Claude can look up members,
                scores and documents itself. Every endpoint below is a tool of the same name,
                answering exactly what the API does. It uses streamable HTTP and needs no sign-in.
              </P>
              <CopyText text={MCP_URL} label="MCP server address" />
              <List>
                <Item label="Claude Code">
                  <span className="block pt-1">
                    <CopyText
                      text={`claude mcp add --transport http civitas ${MCP_URL}`}
                      label="setup command"
                    />
                  </span>
                </Item>
                <Item label="Claude and other apps">
                  Add a custom connector (or remote MCP server) and give it the address above.
                </Item>
                <Item label="A configuration file">
                  <pre className="mt-1 overflow-x-auto border border-white/[0.07] bg-white/[0.02] p-2 font-mono text-xs text-ink-hi">
                    {JSON.stringify(
                      { mcpServers: { civitas: { type: "http", url: MCP_URL } } },
                      null,
                      2
                    )}
                  </pre>
                </Item>
              </List>
            </Section>

            <Section id="reference" title="Endpoints">
              {spec ? (
                <div className="space-y-12">
                  {groups.map(({ tag, description, endpoints }) => (
                    <section key={tag} aria-labelledby={`tag-${tag}`} className="space-y-8">
                      <div className="space-y-1 border-b border-white/[0.07] pb-2">
                        <h3
                          id={`tag-${tag}`}
                          className="font-mono text-xs uppercase tracking-wider text-ink-lo"
                        >
                          {tag}
                        </h3>
                        {description && <p className="text-sm text-ink-mid">{description}</p>}
                      </div>
                      {endpoints.map((e) => (
                        <EndpointBlock key={`${e.method} ${e.path}`} endpoint={e} spec={spec} />
                      ))}
                    </section>
                  ))}
                </div>
              ) : (
                <P>
                  The reference couldn&apos;t be loaded just now. The same description is at{" "}
                  <a
                    href={`${API}/openapi.json`}
                    className="text-signal-cyan underline underline-offset-2"
                  >
                    {`${API}/openapi.json`}
                  </a>
                  .
                </P>
              )}
            </Section>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
