export function readComposer(composer: HTMLElement | null): string {
  if (!composer) return '';
  const isTextarea = (typeof HTMLTextAreaElement !== 'undefined' && composer instanceof HTMLTextAreaElement) || composer.tagName === 'TEXTAREA';
  const isInput = (typeof HTMLInputElement !== 'undefined' && composer instanceof HTMLInputElement) || composer.tagName === 'INPUT';
  if (isTextarea || isInput) return (composer as HTMLTextAreaElement | HTMLInputElement).value || '';
  return composer.innerText || composer.textContent || '';
}

export function writeComposer(composer: HTMLElement | null, text: string): boolean {
  if (!composer) return false;
  const isTextarea = (typeof HTMLTextAreaElement !== 'undefined' && composer instanceof HTMLTextAreaElement) || composer.tagName === 'TEXTAREA';
  const isInput = (typeof HTMLInputElement !== 'undefined' && composer instanceof HTMLInputElement) || composer.tagName === 'INPUT';
  if (isTextarea || isInput) {
    const target = composer as HTMLTextAreaElement | HTMLInputElement;
    const proto = isTextarea
      ? (typeof HTMLTextAreaElement !== 'undefined' ? HTMLTextAreaElement.prototype : Object.getPrototypeOf(target))
      : (typeof HTMLInputElement !== 'undefined' ? HTMLInputElement.prototype : Object.getPrototypeOf(target));
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(target, text);
    else target.value = text;
    target.dispatchEvent(new Event('input', { bubbles: true }));
    target.dispatchEvent(new Event('change', { bubbles: true }));
    return target.value === text;
  }

  if (typeof composer.focus === 'function') {
    composer.focus();
  }
  if (typeof window !== 'undefined' && typeof window.getSelection === 'function' && typeof document !== 'undefined' && typeof document.createRange === 'function') {
    const selection = window.getSelection();
    if (selection) {
      const range = document.createRange();
      range.selectNodeContents(composer);
      selection.removeAllRanges();
      selection.addRange(range);
    }
  }
  const inserted = typeof document !== 'undefined' && typeof document.execCommand === 'function' && document.execCommand('insertText', false, text);
  if (!inserted || readComposer(composer) !== text) {
    composer.textContent = text;
    if (typeof InputEvent !== 'undefined') {
      composer.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
    } else {
      composer.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
  return readComposer(composer) === text;
}
