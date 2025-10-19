package tema1.t03;

import java.util.Scanner;

public class ej11 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.print("Introduce el radio de la circunferencia: ");
        double radio = sc.nextDouble();

        double longitud = 2 * Math.PI * radio;
        double area = Math.PI * radio * radio;

        System.out.println("La longitud es: " + longitud);
        System.out.println("El área es: " + area);
    }
}
