/**
 * Spike-only, API-neutral semantic shape. It keeps original attachment
 * references separate from text/model output; the current backend cannot
 * persist these field-level references yet.
 */
export type ContentPart =
  | { type: "text"; text: string }
  | { type: "image-ref"; ref: string; source: "original"; order: number };

export type SemanticField = { parts: ContentPart[] };

export type SemanticDraft = {
  question: SemanticField;
  user_thoughts: SemanticField;
  reference_answer: SemanticField;
};

export function semanticDraft(
  draft: { question: string; user_thoughts: string; reference_answer: string },
  refs: string[],
): SemanticDraft {
  const field = (text: string): SemanticField => ({
    parts: [
      ...(text ? [{ type: "text" as const, text }] : []),
      ...refs.map((ref, order) => ({ type: "image-ref" as const, ref, source: "original" as const, order })),
    ],
  });
  return {
    question: field(draft.question),
    user_thoughts: field(draft.user_thoughts),
    reference_answer: field(draft.reference_answer),
  };
}
