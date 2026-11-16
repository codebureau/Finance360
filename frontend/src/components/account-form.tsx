import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useDirtyFormProps } from "@/components/ui/dismiss-guard";
import { createAccount, fetchTags, updateAccount, type Account, type AccountInput } from "@/lib/accounts";
import { fetchCatalogue, type Catalogue } from "@/lib/catalogue";
import { ApiError, fetchHousehold, type Member } from "@/lib/household";
import { bpToPercent, evenShares, formatShare, FULL_SHARE_BP, percentToBp } from "@/lib/shares";

/** memberId -> percent text, shared evenly (the joint default). */
function evenSharesText(ids: number[]): Record<number, string> {
  const shares = evenShares(ids.length);
  return Object.fromEntries(ids.map((id, i) => [id, bpToPercent(shares[i])]));
}

function AccountForm({
  initial,
  members,
  catalogue,
  tags,
  onSaved,
}: {
  initial?: Account;
  members: Member[];
  catalogue: Catalogue;
  tags: string[];
  onSaved: () => void;
}) {
  const categories = catalogue.classes.flatMap((c) => c.categories);
  const firstCategory = initial?.category_key ?? categories[0]?.key ?? "";
  const typesOf = (key: string) => categories.find((c) => c.key === key)?.account_types ?? [];

  const [name, setName] = useState(initial?.name ?? "");
  const [category, setCategory] = useState(firstCategory);
  const [accountType, setAccountType] = useState(
    initial?.account_type_key ?? typesOf(firstCategory)[0]?.key ?? "",
  );
  const [currency, setCurrency] = useState(initial?.currency ?? "AUD");
  const [openedOn, setOpenedOn] = useState(initial?.opened_on ?? "");
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [tagList, setTagList] = useState<string[]>(initial?.tags ?? []);
  const [tagText, setTagText] = useState("");
  const [ownership, setOwnership] = useState<"sole" | "joint">(initial?.ownership ?? "sole");
  const [soleOwner, setSoleOwner] = useState<number>(
    initial?.ownership === "sole" ? initial.owners[0].member_id : (members[0]?.id ?? 0),
  );
  const [shares, setShares] = useState<Record<number, string>>(
    initial?.ownership === "joint"
      ? Object.fromEntries(initial.owners.map((o) => [o.member_id, bpToPercent(o.share_bp)]))
      : {},
  );

  const addTag = () => {
    const clean = tagText.replace(/\s+/g, " ").trim();
    if (clean && !tagList.some((t) => t.toLowerCase() === clean.toLowerCase())) {
      setTagList([...tagList, clean]);
    }
    setTagText("");
  };

  const chooseOwnership = (mode: "sole" | "joint") => {
    setOwnership(mode);
    if (mode === "joint" && Object.keys(shares).length < 2) {
      setShares(evenSharesText(members.slice(0, 2).map((m) => m.id)));
    }
  };

  const toggleOwner = (id: number) => {
    const ids = Object.keys(shares).map(Number);
    const next = ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id];
    setShares(evenSharesText(next.sort((a, b) => a - b)));
  };

  const save = useMutation({
    mutationFn: async () => {
      const pending = tagText.replace(/\s+/g, " ").trim();
      const allTags =
        pending && !tagList.some((t) => t.toLowerCase() === pending.toLowerCase())
          ? [...tagList, pending]
          : tagList;
      let owners: AccountInput["owners"];
      if (ownership === "sole") {
        owners = [{ member_id: soleOwner }];
      } else {
        const parsed = Object.entries(shares).map(([id, text]) => ({
          member_id: Number(id),
          bp: percentToBp(text),
        }));
        if (parsed.some((p) => p.bp === null)) {
          throw new ApiError("", {
            owners: "Enter each share as a percentage, e.g. 60 or 33.33.",
          });
        }
        const total = parsed.reduce((sum, p) => sum + (p.bp ?? 0), 0);
        if (total !== FULL_SHARE_BP) {
          throw new ApiError("", {
            owners: `Shares must add up to 100% (they add up to ${formatShare(total)}).`,
          });
        }
        owners = parsed.map((p) => ({ member_id: p.member_id, share_bp: p.bp }));
      }
      const input: AccountInput = {
        name,
        category_key: category,
        account_type_key: accountType,
        currency,
        notes: notes || null,
        opened_on: openedOn || null,
        ownership,
        owners,
        tags: allTags,
      };
      return initial ? updateAccount(initial.id, input) : createAccount(input);
    },
    onSuccess: onSaved,
  });

  const dirtyProps = useDirtyFormProps();

  return (
    <form
      {...dirtyProps}
      aria-label={initial ? `Edit ${initial.name}` : "New Account"}
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Name
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        <FieldMessage error={save.error} field="name" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Category
        <select
          className={inputClass}
          value={category}
          onChange={(e) => {
            setCategory(e.target.value);
            setAccountType(typesOf(e.target.value)[0]?.key ?? "");
          }}
        >
          {categories.map((c) => (
            <option key={c.key} value={c.key}>
              {c.name}
            </option>
          ))}
        </select>
        <FieldMessage error={save.error} field="category" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Account type
        <select
          className={inputClass}
          value={accountType}
          onChange={(e) => setAccountType(e.target.value)}
        >
          {typesOf(category).map((t) => (
            <option key={t.key} value={t.key}>
              {t.name}
            </option>
          ))}
        </select>
        <FieldMessage error={save.error} field="account_type" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Currency
        <input
          className={inputClass}
          value={currency}
          maxLength={3}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
        />
        <FieldMessage error={save.error} field="currency" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Opened on (optional)
        <input
          className={inputClass}
          type="date"
          value={openedOn}
          onChange={(e) => setOpenedOn(e.target.value)}
        />
        <FieldMessage error={save.error} field="opened_on" />
      </label>

      <fieldset className="flex flex-col gap-2 text-sm">
        <legend className="mb-1 font-medium">Ownership</legend>
        <div className="flex gap-4">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="ownership"
              checked={ownership === "sole"}
              onChange={() => chooseOwnership("sole")}
            />
            Sole
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="ownership"
              checked={ownership === "joint"}
              onChange={() => chooseOwnership("joint")}
            />
            Joint
          </label>
        </div>
        {ownership === "sole" ? (
          <select
            aria-label="Member"
            className={inputClass}
            value={soleOwner}
            onChange={(e) => setSoleOwner(Number(e.target.value))}
          >
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        ) : (
          <ul className="flex flex-col gap-2">
            {members.map((m) => (
              <li key={m.id} className="flex items-center gap-3">
                <label className="flex min-w-0 flex-1 items-center gap-2">
                  <input
                    type="checkbox"
                    checked={m.id in shares}
                    onChange={() => toggleOwner(m.id)}
                  />
                  <span className="truncate">{m.name}</span>
                </label>
                {m.id in shares && (
                  <label className="flex items-center gap-1">
                    <input
                      aria-label={`${m.name} share (%)`}
                      className={`${inputClass} w-20`}
                      inputMode="decimal"
                      value={shares[m.id]}
                      onChange={(e) => setShares({ ...shares, [m.id]: e.target.value })}
                    />
                    %
                  </label>
                )}
              </li>
            ))}
          </ul>
        )}
        <FieldMessage error={save.error} field="owners" />
        <FieldMessage error={save.error} field="ownership" />
      </fieldset>

      <div className="flex flex-col gap-2 text-sm">
        <span className="font-medium">Tags</span>
        {tagList.length > 0 && (
          <ul className="flex flex-wrap gap-2">
            {tagList.map((t) => (
              <li
                key={t}
                className="flex items-center gap-1 rounded-full border border-border py-0.5 pr-1 pl-3"
              >
                {t}
                <Button
                  variant="ghost"
                  aria-label={`Remove Tag ${t}`}
                  className="min-h-0 rounded-full px-2"
                  onClick={() => setTagList(tagList.filter((x) => x !== t))}
                >
                  ×
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex gap-2">
          <input
            aria-label="Add a Tag"
            className={`${inputClass} min-w-0 flex-1`}
            list="tag-suggestions"
            value={tagText}
            onChange={(e) => setTagText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addTag();
              }
            }}
          />
          <datalist id="tag-suggestions">
            {tags
              .filter((t) => !tagList.includes(t))
              .map((t) => (
                <option key={t} value={t} />
              ))}
          </datalist>
          <Button variant="secondary" onClick={addTag}>
            Add Tag
          </Button>
        </div>
        <FieldMessage error={save.error} field="tags" />
      </div>

      <label className="flex flex-col gap-1 text-sm">
        Notes
        <textarea
          className={`${inputClass} py-2`}
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        <FieldMessage error={save.error} field="notes" />
      </label>

      <GeneralError error={save.error} />
      <div className="flex justify-end gap-2">
        <SheetClose asChild>
          <Button variant="ghost">Cancel</Button>
        </SheetClose>
        <Button type="submit" disabled={save.isPending}>
          {initial ? "Save" : "Create Account"}
        </Button>
      </div>
    </form>
  );
}

/**
 * Create (no `account`) or edit an Account in a Sheet opened from `trigger`. The form is mounted
 * only while open, so each opening starts from the Account's current values. A failed save keeps
 * the Sheet open with the message beside the field.
 */
export function AccountSheet({
  account,
  trigger,
  onSaved,
}: {
  account?: Account;
  trigger: ReactNode;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const catalogue = useQuery({ queryKey: ["catalogue"], queryFn: fetchCatalogue });
  const tags = useQuery({ queryKey: ["tags"], queryFn: fetchTags });
  const members = household.data?.members ?? [];
  const title = account ? `Edit ${account.name}` : "New Account";

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>{trigger}</SheetTrigger>
      <SheetContent className="sm:max-w-md">
        <SheetTitle className="text-lg font-semibold">{title}</SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          {account ? "Changes apply to this Account only." : "Where it is held, who owns it and what it is."}
        </SheetDescription>
        {household.data && catalogue.data ? (
          members.length === 0 ? (
            <p className="text-sm text-muted-foreground">Add a Member in Household first.</p>
          ) : (
            <AccountForm
              initial={account}
              members={members}
              catalogue={catalogue.data}
              tags={tags.data ?? []}
              onSaved={() => {
                setOpen(false);
                onSaved();
              }}
            />
          )
        ) : (
          <p className="text-muted-foreground">Loading…</p>
        )}
      </SheetContent>
    </Sheet>
  );
}
