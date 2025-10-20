package tema2.t02;

import java.util.Scanner;

public class ej06 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Introduce las ventas que has hecho: ");
        int ventas = sc.nextInt();

        double suma = 0;

        for (int i = 1; i <= ventas; i++){
            System.out.println("Introduce el monto de la venta " + i + ":");
            double monto = sc.nextDouble();
            suma += monto;
        }
        System.out.println("El total de todas las ventas es: " + suma);
    }
}
