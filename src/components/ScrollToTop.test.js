import { act, fireEvent, render, screen } from '@testing-library/react';
import ScrollToTop from './ScrollToTop';

test('the button appears after scrolling down and takes the page back to the top', () => {
  window.scrollTo = jest.fn();
  render(<ScrollToTop />);
  expect(screen.queryByLabelText('맨 위로')).toBeNull();
  act(() => { window.scrollY = 900; window.dispatchEvent(new Event('scroll')); });
  fireEvent.click(screen.getByLabelText('맨 위로'));
  expect(window.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' });
  act(() => { window.scrollY = 0; window.dispatchEvent(new Event('scroll')); });
  expect(screen.queryByLabelText('맨 위로')).toBeNull();
});
