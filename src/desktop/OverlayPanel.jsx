import React from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';

export default function OverlayPanel({ title, description, children, footer, className, label }) {
  return <Card className={className} aria-label={label}>
    <CardHeader>
      <CardTitle asChild><h3>{title}</h3></CardTitle>
      {description && <CardDescription><p>{description}</p></CardDescription>}
    </CardHeader>
    <CardContent>{children}</CardContent>
    {footer && <CardFooter>{footer}</CardFooter>}
  </Card>;
}
