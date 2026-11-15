import { Fragment, type ReactNode } from 'react';

/**
 * Minimal, safe markdown: paragraphs, **bold**, `inline code`, -/* and 1. lists.
 * Everything is emitted as React elements/text, never as HTML strings.
 */
function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;
  let last = 0;
  let i = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('`')) out.push(<code key={`${keyBase}-${i++}`}>{tok.slice(1, -1)}</code>);
    else out.push(<strong key={`${keyBase}-${i++}`}>{tok.slice(2, -2)}</strong>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block = { type: 'p'; lines: string[] } | { type: 'ul' | 'ol'; items: string[] };

export function parseBlocks(src: string): Block[] {
  const blocks: Block[] = [];
  let cur: Block | null = null;
  for (const raw of src.replace(/\r\n?/g, '\n').split('\n')) {
    const line = raw.trimEnd();
    const ul = /^\s*[-*•]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (line.trim() === '') {
      cur = null;
    } else if (ul || ol) {
      const type = ul ? 'ul' : 'ol';
      if (!cur || cur.type !== type) {
        cur = { type, items: [] };
        blocks.push(cur);
      }
      cur.items.push((ul ?? ol)![1]);
    } else if (cur && cur.type === 'p') {
      cur.lines.push(line);
    } else {
      cur = { type: 'p', lines: [line] };
      blocks.push(cur);
    }
  }
  return blocks;
}

export function Markdown({ text }: { text: string }) {
  const blocks = parseBlocks(text);
  return (
    <>
      {blocks.map((b, i) => {
        if (b.type === 'p')
          return (
            <p key={i}>
              {b.lines.map((l, j) => (
                <Fragment key={j}>
                  {j > 0 && <br />}
                  {inline(l, `${i}-${j}`)}
                </Fragment>
              ))}
            </p>
          );
        const Tag = b.type;
        return (
          <Tag key={i}>
            {b.items.map((it, j) => (
              <li key={j}>{inline(it, `${i}-${j}`)}</li>
            ))}
          </Tag>
        );
      })}
    </>
  );
}
