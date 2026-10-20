import React from 'react';
import { render, screen } from '@testing-library/react';
import CornerMap from './CornerMap';

const points = Array.from({ length: 11 }, (_, index) => ({ distance_pct: index / 10, x: index * 20,
  y: index * 5, time_s: index, breakBefore: index === 6 }));
const props = () => ({ projected: points, referenceProjected: points.map(point => ({ ...point, y: point.y + 5 })),
  corners: [], onSelect: jest.fn(), onHover: jest.fn() });

test('unregistered LMU paths remain schematic and expose no satellite layer or registration claim', () => {
  render(<CornerMap {...props()} schematic mapStatus="schematic" />);
  expect(screen.getByTestId('map-background-source')).toHaveTextContent('기록 좌표 개략도');
  expect(screen.getByTestId('map-background-source')).toHaveTextContent('실제 지리 위치·도로 경계는 확인하지 않았습니다');
  expect(screen.queryByTestId('map-estimated-alignment')).not.toBeInTheDocument();
  expect(screen.queryByTestId('map-geographic-background')).not.toBeInTheDocument();
  expect(screen.queryByRole('radio', { name: '위성 지도' })).not.toBeInTheDocument();
  expect(screen.getAllByTestId('map-reference-line')).toHaveLength(2);
});
