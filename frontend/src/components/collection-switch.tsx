import { LayoutGrid, List } from "lucide-react";
import { useId, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { SegmentedControl, type SegmentOption } from "@/components/ui/segmented-control";
import { useDisplayPreference } from "@/lib/display-preferences";

/** The forms of a `collection` (ADR-0031): cards first, so cards are the kit default. */
export type CollectionForm = "cards" | "list";
export const COLLECTION_FORMS: readonly CollectionForm[] = ["cards", "list"];
export const COLLECTION_SHAPE = "collection";

const OPTIONS: SegmentOption<CollectionForm>[] = [
  { value: "cards", label: "Cards", icon: LayoutGrid },
  { value: "list", label: "List", icon: List },
];

/**
 * The form of a `collection` view and its switch: the Cards and List radio group with its own
 * "Use as my default" (Display preferences, ADR-0031 section 3, shape `collection`). The caller
 * places `control` in the view's toolbar and renders `form`; nothing is drawn until `ready`.
 */
export function useCollectionForm(viewKey: string): { form: CollectionForm; ready: boolean; control: ReactNode } {
  const groupId = useId();
  const pref = useDisplayPreference<CollectionForm>(viewKey, COLLECTION_SHAPE, COLLECTION_FORMS);
  const control = (
    <>
      <SegmentedControl
        label="Show as"
        name={`${viewKey}-form-${groupId}`}
        value={pref.form}
        options={OPTIONS}
        onChange={pref.choose}
      />
      {pref.canUseAsDefault && (
        <Button variant="outline" onClick={pref.makeDefault}>
          Use as my default
        </Button>
      )}
    </>
  );
  return { form: pref.form, ready: pref.ready, control };
}
