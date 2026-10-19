import { fireEvent, render, screen } from '@testing-library/react';
import { SectionNav } from './WorkspaceShell';

test('the section list opens and scrolls to the panel', () => {
  const target = document.createElement('section');
  target.id = 'corner-table';
  target.scrollIntoView = jest.fn();
  document.body.appendChild(target);
  const events = [];
  const listen = (event) => events.push(event.detail);
  window.addEventListener('coach-reveal-section', listen);
  const raf = jest.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => { callback(0); return 0; });
  render(<SectionNav sections={[{ id: 'corner-names', label: '코너 번호 확인' }, { id: 'corner-table', label: '코너 자동 분할 및 위상별 비교' }]} />);
  fireEvent.click(screen.getByText('코너 자동 분할 및 위상별 비교'));
  expect(events).toEqual(['corner-table']);
  expect(target.scrollIntoView).toHaveBeenCalled();
  expect(screen.getByText('코너 자동 분할 및 위상별 비교')).toHaveAttribute('aria-current', 'location');
  window.removeEventListener('coach-reveal-section', listen);
  raf.mockRestore();
  target.remove();
});

test('no list without sections', () => {
  const { container } = render(<SectionNav sections={null} />);
  expect(container).toBeEmptyDOMElement();
});
