import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

export type DropdownOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

export function Dropdown({ options, value, defaultValue, onChange, id, name, ariaLabel, disabled, required }: {
  options: DropdownOption[];
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  id?: string;
  name?: string;
  ariaLabel?: string;
  disabled?: boolean;
  required?: boolean;
}) {
  const generatedID = useId();
  const triggerID = id ?? `dropdown-${generatedID}`;
  const [internalValue, setInternalValue] = useState(defaultValue ?? options.find(option => !option.disabled)?.value ?? "");
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const selectedValue = value ?? internalValue;
  const selected = options.find(option => option.value === selectedValue);

  useEffect(() => {
    if (value !== undefined || options.some(option => option.value === internalValue)) return;
    setInternalValue(options.find(option => !option.disabled)?.value ?? "");
  }, [internalValue, options, value]);

  useEffect(() => {
    if (!open) return;
    (list.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]:not(:disabled)') ?? list.current?.querySelector<HTMLButtonElement>("button:not(:disabled)"))?.focus();
  }, [open]);

  function choose(nextValue: string) {
    if (value === undefined) setInternalValue(nextValue);
    onChange?.(nextValue);
    setOpen(false);
    trigger.current?.focus();
  }

  function move(event: KeyboardEvent<HTMLButtonElement>, offset: number) {
    event.preventDefault();
    const enabled = [...(list.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
    const index = enabled.indexOf(event.currentTarget);
    enabled[(index + offset + enabled.length) % enabled.length]?.focus();
  }

  return <div className={`dropdown${open ? " open" : ""}`} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    {name && <input className="dropdownValue" name={name} value={selectedValue} required={required} disabled={disabled} readOnly tabIndex={-1} aria-hidden="true" onInvalid={event => { event.preventDefault(); setOpen(true); trigger.current?.focus(); }} />}
    <button ref={trigger} id={triggerID} type="button" className="dropdownTrigger" aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} aria-controls={`${triggerID}-options`} aria-required={required} disabled={disabled} onClick={() => setOpen(current => !current)} onKeyDown={event => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setOpen(true); }
      if (event.key === "Escape") setOpen(false);
    }}><span>{selected?.label ?? "선택"}</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4" /></svg></button>
    {open && <div ref={list} id={`${triggerID}-options`} className="dropdownMenu" role="listbox" aria-labelledby={ariaLabel ? undefined : triggerID} aria-label={ariaLabel}>
      {options.map(option => <button type="button" className="dropdownOption" role="option" aria-selected={option.value === selectedValue} key={option.value} disabled={option.disabled} title={option.label} onClick={() => choose(option.value)} onKeyDown={event => {
        if (event.key === "ArrowDown") move(event, 1);
        if (event.key === "ArrowUp") move(event, -1);
        if (event.key === "Escape") { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
      }}><span>{option.label}</span>{option.value === selectedValue && <b aria-hidden="true">✓</b>}</button>)}
    </div>}
  </div>;
}
