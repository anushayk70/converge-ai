import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Renders report/chat markdown. Links of the form (cite:u_xxxx) become clickable citation chips that open the source passage.
export default function Markdown({ text, citations = {}, onCite }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(u) => u}
        components={{
          a({ href, children }) {
            if (href && href.startsWith("cite:")) {
              const id = href.slice(5);
              const known = !!citations[id];
              return (
                <a className={"cite" + (known ? "" : " bad")} href="#" title={known ? "Show source passage" : "Passage not available"}
                   onClick={(e) => { e.preventDefault(); if (known && onCite) onCite(id); }}>{children}</a>
              );
            }
            const safe = href && /^(https?:|mailto:|#)/i.test(href);
            return <a href={safe ? href : undefined} target="_blank" rel="noreferrer noopener">{children}</a>;
          },
        }}
      >{text}</ReactMarkdown>
    </div>
  );
}
