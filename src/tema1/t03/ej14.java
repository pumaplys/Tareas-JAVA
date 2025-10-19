package tema1.t03;

import java.util.Scanner;

public class ej14 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.print("Teclea la cantidad de euros: ");
        int cantidad = sc.nextInt();

        int billetes500 = cantidad / 500;
        cantidad = cantidad % 500;
        int billetes200 = cantidad / 200;
        cantidad = cantidad % 200;
        int billetes100 = cantidad / 100;
        cantidad = cantidad % 100;
        int billetes50 = cantidad / 50;
        cantidad = cantidad % 50;
        int billetes20 = cantidad / 20;
        cantidad = cantidad % 20;
        int billetes10 = cantidad / 10;
        cantidad = cantidad % 10;
        int billetes5 = cantidad / 5;
        cantidad = cantidad % 5;

        System.out.println("La cantidad de billetes que te tengo que dar es:");
        System.out.println(billetes500 + " billetes de 500€");
        System.out.println(billetes200 + " billetes de 200€");
        System.out.println(billetes100 + " billetes de 100€");
        System.out.println(billetes50 + " billetes de 50€");
        System.out.println(billetes20 + " billetes de 20€");
        System.out.println(billetes10 + " billetes de 10€");
        System.out.println(billetes5 + " billetes de 5€");
    }
}
